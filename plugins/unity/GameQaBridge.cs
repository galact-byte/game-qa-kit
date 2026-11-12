using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using BepInEx;
using UnityEngine;
using UnityEngine.EventSystems;
using UnityEngine.SceneManagement;
using UnityEngine.UI;
#if IL2CPP
using BepInEx.Unity.IL2CPP.Utils;
using System.Linq;
#endif

namespace GameQaKit.Unity
{
#if !IL2CPP
    [BepInPlugin("org.gameqakit.unity.bridge","Game QA Unity Bridge","0.1.0")]
    [DefaultExecutionOrder(-32000)]
    public sealed class GameQaBridge : BaseUnityPlugin
#else
    public sealed class GameQaBridge : MonoBehaviour
#endif
    {
#if IL2CPP
        public GameQaBridge(IntPtr pointer) : base(pointer) { }
        private BepInEx.Logging.ManualLogSource BridgeLog { get { return Il2CppEntry.RuntimeLog; } }
        private const string Engine="unity_il2cpp";
#else
        private BepInEx.Logging.ManualLogSource BridgeLog { get { return Logger; } }
        private const string Engine="unity_mono";
#endif
        private string directory,executable,session;
        private int pid;
        private ControlCore core;
        private IInputBackend router;
        private bool installed;
        private double lastStatus,lastObservation;
        private long observationSequence;
        private string observationError;
        private static double Now { get { return (DateTime.UtcNow-new DateTime(1970,1,1,0,0,0,DateTimeKind.Utc)).TotalSeconds; } }
        private string FileAt(string name) { return Path.Combine(directory,name); }
        private Dictionary<string,string> Read(string name) { return Wire.ReadIni(FileAt(name+".ini")); }
        private void Awake()
        {
            directory=Environment.GetEnvironmentVariable("GAMEQAKIT_BRIDGE_DIR");
            session=Environment.GetEnvironmentVariable("GAMEQAKIT_SESSION_ID");
            if(string.IsNullOrEmpty(directory) || string.IsNullOrEmpty(session)) { enabled=false; return; }
            try
            {
                if(!DesktopGuard.Matches(Environment.GetEnvironmentVariable("GAMEQAKIT_DESKTOP_NAME")))
                    throw new InvalidOperationException("Unity bridge requires the bound private Win32 desktop");
                directory=Path.GetFullPath(directory);
                var meta=Read("connection");
                pid=Process.GetCurrentProcess().Id;
                executable=Process.GetCurrentProcess().MainModule.FileName;
                if(Wire.Get(meta,"protocol")!="1" || Wire.Get(meta,"session_id")!=session ||
                    Wire.Get(meta,"engine")!=Engine || Wire.Get(meta,"command_format")!="ini" ||
                    Wire.Get(meta,"pid")!=pid.ToString(System.Globalization.CultureInfo.InvariantCulture) ||
                    !string.Equals(Path.GetFullPath(Wire.Get(meta,"exe_path")),Path.GetFullPath(executable),StringComparison.OrdinalIgnoreCase))
                    throw new InvalidOperationException("Bridge target/session mismatch");
                Directory.CreateDirectory(FileAt("results")); Directory.CreateDirectory(FileAt("screenshots"));
                core=new ControlCore(session);
#if !IL2CPP
                LegacyInput.Core=core;
#endif
                Application.runInBackground=true;
#if !IL2CPP
                SceneManager.sceneLoaded+=OnSceneLoaded;
#endif
                this.StartCoroutine(AfterRender());
                WriteStatus(true);
            }
            catch(Exception error) { BridgeLog.LogError(error); enabled=false; }
        }
        private void Start()
        {
            if(core!=null && SceneManager.GetActiveScene().isLoaded) Install();
        }
        private void OnSceneLoaded(Scene scene,LoadSceneMode mode) { Install(); }
        private void Install()
        {
            if(installed || core==null) return;
            installed=true;
            try
            {
                string backend=Environment.GetEnvironmentVariable("GAMEQAKIT_INPUT_BACKEND")??"legacy";
                if(backend=="input_system")
                {
                    string file=Path.Combine(Path.GetDirectoryName(GetType().Assembly.Location),"GameQaInputSystem.dll");
                    var assembly=System.Reflection.Assembly.LoadFrom(file);
                    router=(IInputBackend)Activator.CreateInstance(assembly.GetType("GameQaKit.Unity.InputSystemBackend",true),new object[]{core});
                    router.BeforeUpdate+=Tick;
                }
#if IL2CPP
                else throw new NotSupportedException("IL2CPP currently requires the input_system backend");
#else
                else if(backend=="rewired")
                {
                    string file=Path.Combine(Path.GetDirectoryName(GetType().Assembly.Location),"GameQaRewired.dll");
                    var assembly=System.Reflection.Assembly.LoadFrom(file);
                    router=(IInputBackend)Activator.CreateInstance(assembly.GetType("GameQaKit.Unity.RewiredBackend",true),new object[]{core,Path.Combine(Application.dataPath,"Managed")});
                }
                else if(backend=="legacy") router=new LegacyBackend(Path.Combine(Application.dataPath,"Managed"));
                else throw new NotSupportedException("Unknown input backend: "+backend);
#endif
                BridgeLog.LogInfo("Input backend: "+router.Name+"; patched methods: "+router.PatchedMethods);
            }
            catch(Exception error)
            {
                if(router!=null) router.Dispose();
                core.Fail("input_backend_unavailable: "+error.Message); BridgeLog.LogError(error);
            }
        }
        private double Lease()
        {
            var lease=Read("lease");
            return Wire.Get(lease,"session_id")==session ? Wire.Number(lease,"expires_at") : 0;
        }
        private bool Ready { get { return router!=null && router.Ready && SceneManager.GetActiveScene().isLoaded && Screen.width>0 && Screen.height>0; } }
        private void Update()
        {
            Application.runInBackground=true;
            if(router==null || !router.DrivesFrames) Tick();
        }
        private void Tick()
        {
            if(core==null) return;
            try
            {
                bool wasPaused=core.Paused;
                // Read the command before its authorization snapshots: the host
                // publishes its lease first, and a newer pause must take priority.
                var fields=core.Active==null && core.PendingReceipt==null ? Read("request") : null;
                double now=Now,expiry=Lease();
                core.BeginFrame(now,expiry,Wire.PauseId(Read("pause"),session));
                if(fields!=null)
                {
                    if(Wire.Get(fields,"session_id")==session && Wire.Get(fields,"protocol")=="1")
                    {
                        var request=Wire.ParseCommand(fields);
                        if(core.Accept(request,now,expiry))
                        {
                            if(!Ready) core.Fail("input_backend_unavailable");
                            else if(request.Action=="click" && (request.X>=Screen.width || request.Y>=Screen.height)) core.Fail("click_out_of_bounds");
                        }
                    }
                }
                if(!wasPaused && core.Paused && (router==null || !router.DrivesFrames)) UnityEngine.Input.ResetInputAxes();
                if(router!=null) router.Advance(Time.unscaledDeltaTime);
                FlushReceipt(); WriteStatus(false);
            }
            catch(Exception error) { core.Fail("bridge_io: "+error.Message); TryReport(); }
        }
        private IEnumerator AfterRender()
        {
            while(true)
            {
                yield return new WaitForEndOfFrame();
                if(core==null) continue;
                try
                {
                    if(core.EndFrame())
                    {
                        var scene=SceneManager.GetActiveScene();
                        if(!scene.isLoaded) continue;
                        string relative="screenshots/"+core.ActiveId+".png";
                        Capture(FileAt(relative));
                        Wire.Write(FileAt("screenshots/startup.png"),File.ReadAllBytes(FileAt(relative)));
                        observationSequence++; lastObservation=Now; observationError=null;
                        core.Captured(relative);
                    }
                    else if(Ready && observationError==null && (observationSequence==0 || Now-lastObservation>=0.5))
                    {
                        Capture(FileAt("screenshots/startup.png"));
                        observationSequence++; lastObservation=Now;
                    }
                    FlushReceipt(); WriteStatus(false);
                }
                catch(Exception error)
                {
                    observationSequence=0; observationError=error.Message;
                    core.Fail("capture_failed: "+error.Message); TryReport();
                }
            }
        }
        private void Capture(string target)
        {
            if(Screen.width<1 || Screen.height<1 || (long)Screen.width*Screen.height>16777216)
                throw new InvalidOperationException("Invalid capture dimensions");
            Texture2D texture=null;
            try
            {
                texture=new Texture2D(Screen.width,Screen.height,TextureFormat.RGB24,false);
                texture.ReadPixels(new Rect(0,0,Screen.width,Screen.height),0,0,false); texture.Apply();
#if IL2CPP
                byte[] png=ImageConversion.EncodeToPNG(texture).ToArray();
#else
                byte[] png=texture.EncodeToPNG();
#endif
                if(png==null || png.Length>16*1024*1024) throw new InvalidOperationException("Invalid PNG size");
                Wire.Write(target,png);
            }
            finally { if(texture!=null) UnityEngine.Object.Destroy(texture); }
        }
        private Dictionary<string,object> View()
        {
            var roots=new List<object>();
            var scene=SceneManager.GetActiveScene();
            if(scene.isLoaded)
            {
                foreach(var root in scene.GetRootGameObjects())
                {
                    if(roots.Count==64) break;
                    var p=root.transform.position;
                    roots.Add(new Dictionary<string,object>{{"name",root.name},{"position",new[]{p.x,p.y,p.z}}});
                }
            }
            var text=new List<string>();
            foreach(var item in Resources.FindObjectsOfTypeAll<Text>())
            {
                if(!item.gameObject.scene.isLoaded || !item.gameObject.activeInHierarchy) continue;
                if(text.Count==32) break;
                string value=item.text??""; text.Add(value.Length>512 ? value.Substring(0,512) : value);
            }
            var selected=EventSystem.current==null ? null : EventSystem.current.currentSelectedGameObject;
            return new Dictionary<string,object> { {"scene",scene.name},{"root_transforms",roots},{"ui_text",text},
                {"selected",selected==null ? null : selected.name} };
        }
        private void FlushReceipt()
        {
            var result=core.PendingReceipt;
            if(result==null) return;
            var record=new Dictionary<string,object> { {"protocol",1},{"session_id",session},{"id",result.Request.Id},
                {"ok",result.Ok},{"consumed",result.Consumed},{"frame",result.Frame},{"capture_frame",result.CaptureFrame},
                {"paused",core.Paused},{"error",result.Error},{"screenshot",result.Screenshot},{"state",View()} };
            WriteStatus(true);
            Wire.WriteJson(FileAt("results/"+result.Request.Id+".json"),record);
            core.AcknowledgeReceipt();
        }
        private void WriteStatus(bool force)
        {
            double now=Now;
            if(!force && now-lastStatus<0.2) return;
            var state=View();
            state["protocol"]=1; state["session_id"]=session; state["pid"]=pid; state["exe_path"]=executable;
            state["engine"]=Engine; state["input_backend"]=router==null ? null : router.Name;
            state["unity_version"]=Application.unityVersion; state["company"]=Application.companyName;
            state["product"]=Application.productName; state["persistent_data_path"]=Application.persistentDataPath;
            state["updated_at"]=now; state["frame"]=core.Frame; state["paused"]=core.Paused;
            state["held"]=core.HeldButtons; state["input_ready"]=Ready && observationError==null;
            state["observation_seq"]=observationSequence; state["observation_error"]=observationError;
            state["screen"]=new[]{Screen.width,Screen.height}; state["error"]=core.Error;
            state["command_id"]=core.ActiveId; state["patched_methods"]=router==null ? 0 : router.PatchedMethods;
            state["capabilities"]=Ready ? new[]{"move","confirm","cancel","wait","press","release","click"} : new string[0];
            Wire.WriteState(FileAt("status.json"),state); lastStatus=now;
        }
        private void TryReport()
        {
            try { FlushReceipt(); WriteStatus(true); }
            catch(Exception error) { BridgeLog.LogError(error); }
        }
        private void OnDestroy()
        {
            if(core==null) return;
            core.Fail("plugin_unloaded"); TryReport();
#if !IL2CPP
            LegacyInput.Core=null;
#endif
#if !IL2CPP
            SceneManager.sceneLoaded-=OnSceneLoaded;
#endif
            if(router!=null) router.Dispose();
        }
    }
}
