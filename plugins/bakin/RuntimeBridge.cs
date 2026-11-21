using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Imaging;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Yukar.Engine;

namespace GameQaKit.Bakin
{
    public static class RuntimeBridge
    {
        [DllImport("kernel32.dll")] static extern ulong GetTickCount64();
        [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        static extern IntPtr OpenFileMappingW(uint desiredAccess, bool inheritHandle, string name);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern IntPtr MapViewOfFile(IntPtr mapping, uint desiredAccess, uint highOffset, uint lowOffset, UIntPtr bytes);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool UnmapViewOfFile(IntPtr view);
        [DllImport("kernel32.dll", SetLastError=true)]
        static extern bool CloseHandle(IntPtr handle);
        const int SharedSlotSize = 65536;
        const int SharedSlotMaxBytes = SharedSlotSize - 8;
        const int SharedSlotCount = 4;
        static IntPtr sharedMapping, sharedView;
        static readonly object Gate = new object();
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength=65536, RecursionLimit=8 };
        static readonly FieldInfo TitleState = typeof(TitleScene).GetField("state",BindingFlags.Instance|BindingFlags.NonPublic);
        static readonly FieldInfo MapFade = typeof(MapScene).GetField("fadeStatus",BindingFlags.Instance|BindingFlags.NonPublic);
        static string titleState="UNKNOWN", mapFade="UNKNOWN";
        static bool mapLoading=true;
        static ChannelState channel;
        static bool ready;
        static string root, scene="NONE", failure;
        static ulong lastIo=GetTickCount64(), lastStatusIo=GetTickCount64(), beforeInputAt;
        static long drawSequence, captureSequence;
        static Capture pending, completed, lastCapture;
        static bool captureBusy;
        static readonly AutoResetEvent CaptureAvailable = new AutoResetEvent(false);
        static int ioFailed;
        sealed class IoStep
        {
            public readonly string Operation, Path, Temporary;
            public readonly ulong At=GetTickCount64();
            public readonly Exception Error;
            public IoStep(string operation, string path, string temporary, Exception error=null)
            { Operation=operation; Path=path; Temporary=temporary; Error=error; }
        }
        sealed class IoTrace
        {
            public volatile IoStep Current, Failure;
        }
        static readonly IoTrace ControlIo=new IoTrace(), CaptureIo=new IoTrace(), StatusIo=new IoTrace(), DiagnosticIo=new IoTrace();
        sealed class RuntimePulse
        {
            public readonly ulong At=GetTickCount64(), Previous;
            public readonly int Gen0=GC.CollectionCount(0), Gen1=GC.CollectionCount(1), Gen2=GC.CollectionCount(2);
            public RuntimePulse(ulong previous) { Previous=previous; }
        }
        static volatile RuntimePulse runtimePulse, longestPulse;
        static object RuntimeValue()
        {
            var pulse=runtimePulse; var longest=longestPulse;
            if(pulse==null) return null;
            return new Dictionary<string,object> {{"at_ms",pulse.At},{"age_ms",GetTickCount64()-pulse.At},
                {"max_gap_ms",longest==null ? 0 : longest.At-longest.Previous},
                {"max_gap_at_ms",longest==null ? 0 : longest.At},
                {"gc_gen0",pulse.Gen0},{"gc_gen1",pulse.Gen1},{"gc_gen2",pulse.Gen2}};
        }
        static void RuntimeHeartbeat()
        {
            // No channel I/O or input lock: filesystem stalls must not look like a CLR-wide pause.
            while(true)
            {
                var previous=runtimePulse;
                var pulse=new RuntimePulse(previous==null ? GetTickCount64() : previous.At);
                var longest=longestPulse;
                if(longest==null || pulse.At-pulse.Previous>longest.At-longest.Previous) longestPulse=pulse;
                runtimePulse=pulse;
                Thread.Sleep(25);
            }
        }
        [ThreadStatic] static IoTrace threadIo;
        static void TraceIo(string operation, string path=null, string temporary=null)
        {
            if(threadIo!=null) threadIo.Current=new IoStep(operation,path,temporary);
        }
        static void TraceError(Exception error)
        {
            if(threadIo==null || threadIo.Failure!=null) return;
            var step=threadIo.Current;
            threadIo.Failure=new IoStep(step==null ? "unknown" : step.Operation,
                step==null ? null : step.Path,step==null ? null : step.Temporary,error);
        }
        static object IoValue(IoStep step)
        {
            if(step==null) return null;
            string detail=step.Error==null ? null : step.Error.Message;
            if(detail!=null && detail.Length>512) detail=detail.Substring(0,512);
            return new Dictionary<string,object> {{"operation",step.Operation},{"path",step.Path},
                {"temporary",step.Temporary},{"at_ms",step.At},{"age_ms",GetTickCount64()-step.At},
                {"error_type",step.Error==null ? null : step.Error.GetType().Name},
                {"hresult",step.Error==null ? 0 : step.Error.HResult},{"error",detail}};
        }
        static object IoValue(IoTrace trace)
        {
            var current=(Dictionary<string,object>)IoValue(trace.Current) ?? new Dictionary<string,object>();
            current.Add("failure",IoValue(trace.Failure));
            return current;
        }
        static void OpenSharedMemory(int pid, string session)
        {
            string name="Local\\gameqakit-bakin-"+pid.ToString(System.Globalization.CultureInfo.InvariantCulture)+"-"+session;
            IntPtr mapping=OpenFileMappingW(0x0006,false,name);
            if(mapping==IntPtr.Zero) throw new IOException("Missing realtime shared memory ("+Marshal.GetLastWin32Error()+")");
            IntPtr view=MapViewOfFile(mapping,0x0006,0,0,(UIntPtr)(SharedSlotCount*SharedSlotSize));
            if(view==IntPtr.Zero)
            {
                int err=Marshal.GetLastWin32Error();
                CloseHandle(mapping);
                throw new IOException("MapViewOfFile failed ("+err+")");
            }
            sharedMapping=mapping; sharedView=view;
        }
        static void WriteShared(int slotIndex, object value)
        {
            IntPtr view=sharedView;
            if(view==IntPtr.Zero) throw new IOException("Shared memory not open");
            var serializer=new JavaScriptSerializer { MaxJsonLength=65536, RecursionLimit=8 };
            byte[] bytes=new UTF8Encoding(false).GetBytes(serializer.Serialize(value));
            if(bytes.Length>SharedSlotMaxBytes) throw new IOException("Oversized shared message");
            IntPtr slot=IntPtr.Add(view,slotIndex*SharedSlotSize);
            int before=Marshal.ReadInt32(slot);
            if((before&1)!=0) throw new IOException("Shared slot writer busy");
            int odd=unchecked(before+1);
            int even=unchecked(before+2);
            if(even==0) even=2;
            Marshal.WriteInt32(slot,odd);
            Thread.MemoryBarrier();
            Marshal.Copy(bytes,0,IntPtr.Add(slot,8),bytes.Length);
            Marshal.WriteInt32(IntPtr.Add(slot,4),bytes.Length);
            Thread.MemoryBarrier();
            Marshal.WriteInt32(slot,even);
            Thread.MemoryBarrier();
        }
        static Dictionary<string,object> ReadShared(int slotIndex)
        {
            IntPtr view=sharedView;
            if(view==IntPtr.Zero) return null;
            IntPtr slot=IntPtr.Add(view,slotIndex*SharedSlotSize);
            for(int attempt=0; attempt<8; attempt++)
            {
                int before=Marshal.ReadInt32(slot);
                Thread.MemoryBarrier();
                if(before==0) return null;
                if((before&1)!=0) { Thread.SpinWait(16); continue; }
                int length=Marshal.ReadInt32(IntPtr.Add(slot,4));
                if(length<=0 || length>SharedSlotMaxBytes)
                {
                    Thread.MemoryBarrier();
                    if(Marshal.ReadInt32(slot)!=before) continue;
                    throw new IOException("Oversized shared message");
                }
                byte[] bytes=new byte[length];
                Marshal.Copy(IntPtr.Add(slot,8),bytes,0,length);
                Thread.MemoryBarrier();
                int after=Marshal.ReadInt32(slot);
                if(after==before)
                {
                    var serializer=new JavaScriptSerializer { MaxJsonLength=65536, RecursionLimit=8 };
                    return serializer.Deserialize<Dictionary<string,object>>(Encoding.UTF8.GetString(bytes));
                }
            }
            return null;
        }
        static void DiagnosticWorker()
        {
            threadIo=DiagnosticIo;
            // This thread never takes the input lock; its own I/O cannot stall control or capture.
            string lastDiskFaultKey=null;
            while(true)
            {
                try
                {
                    var value=Identity(); value.Add("schema_version",1);
                    value.Add("published_at_ms",GetTickCount64());
                    value.Add("control",IoValue(ControlIo)); value.Add("capture",IoValue(CaptureIo));
                    value.Add("status",IoValue(StatusIo)); value.Add("runtime",RuntimeValue());
                    WriteShared(2,value);
                    string faultKey=(ControlIo.Failure==null ? "0" : "1")+(CaptureIo.Failure==null ? "0" : "1")
                        +(StatusIo.Failure==null ? "0" : "1")+(Volatile.Read(ref ioFailed)!=0 ? "1" : "0");
                    if(faultKey!="0000" && faultKey!=lastDiskFaultKey)
                    {
                        Publish("target-io.json",value);
                        lastDiskFaultKey=faultKey;
                    }
                }
                catch(Exception) { }
                Thread.Sleep(25);
            }
        }
        static string ioPhase="initializing", ioMaxPhase, ioStallPhase;
        static long ioPhaseAt, ioMaxPhaseMs, ioStallMs;
        static object ioStallDetail;
        static void Phase(string name)
        {
            if(threadIo==StatusIo) return;
            long now=(long)GetTickCount64(), previous=Interlocked.Exchange(ref ioPhaseAt,now);
            if(previous!=0 && now-previous>Interlocked.Read(ref ioMaxPhaseMs))
            { ioMaxPhase=ioPhase; Interlocked.Exchange(ref ioMaxPhaseMs,now-previous); }
            ioPhase=name;
        }
        sealed class Capture
        {
            public long Sequence, DrawSequence;
            public string Id, Scene, TitleState, MapFade;
            public Dictionary<string,object> InputSnapshot;
            public bool MapLoading;
            public ulong Expiry, At;
            public Bitmap Image;
            public string Phase, Error;
            public ulong AcceptedAt, PhaseAt;
        }
        static void CapturePhase(Capture value, string phase)
        {
            lock(Gate) { value.Phase=phase; value.PhaseAt=GetTickCount64(); }
        }
        static void CaptureError(Capture value, Exception error)
        {
            if(value == null) return;
            string detail=error.GetType().Name+": "+error.Message;
            lock(Gate) { value.Error=detail.Length>512 ? detail.Substring(0,512) : detail; }
        }
        static RuntimeBridge()
        {
            // Starting a worker is the only initialization on the engine thread.
            var thread=new Thread(Worker); thread.IsBackground=true; thread.Name="GameQaKit.Bakin.IO"; thread.Start();
        }
        static void Guard()
        {
            ulong now=GetTickCount64();
            if (channel != null && (Volatile.Read(ref ioFailed) != 0 || now-lastIo>1000 || now-lastStatusIo>1000))
            {
                if((now-lastIo>1000 || now-lastStatusIo>1000) && ioStallPhase==null)
                {
                    ioStallPhase=now-lastStatusIo>1000 ? "status_publish" : ioPhase;
                    Interlocked.Exchange(ref ioStallMs,now-lastStatusIo>1000 ? (long)(now-lastStatusIo) : (long)now-Interlocked.Read(ref ioPhaseAt));
                    ioStallDetail=new Dictionary<string,object> {{"observed_at_ms",now},
                        {"control",IoValue(ControlIo)},{"status",IoValue(StatusIo)},
                        {"diagnostic",IoValue(DiagnosticIo)},{"runtime",RuntimeValue()}};
                }
                channel.Fault(failure ?? "io_stalled");
            }
            if (channel != null) channel.Tick(now);
        }
        public static void BeforeInput()
        {
            Input.SuppressCursorControl=true;
            lock(Gate) { Guard(); beforeInputAt=GetTickCount64(); }
        }
        public static void AfterInput()
        {
            lock(Gate)
            {
                Guard();
                if (channel == null) return;
                ulong now=GetTickCount64();
                if (beforeInputAt != 0 && now-beforeInputAt>1000) channel.Fault("input_update_stalled");
                channel.Input.Sample(now);
            }
        }
        public static Input.KeyStates GetState(Input.StateType type, Input.GameState context)
        {
            lock(Gate) { Guard(); return channel == null ? Input.KeyStates.NONE : (Input.KeyStates)channel.Input.Query((int)type,(int)context,GetTickCount64()); }
        }
        public static float GetAxis(Input.KeyStates key,Input.GameState context,bool deadzone,bool threshold)
        {
            lock(Gate) { Guard(); return channel == null ? 0 : channel.Input.Axis((ulong)key,(int)context,GetTickCount64()); }
        }
        public static void ConsumeTrigger(Input.KeyStates key,Input.GameState context)
        {
            lock(Gate) { if(channel != null) channel.Input.ConsumeTrigger((ulong)key,(int)context); }
        }
        public static void AfterDraw()
        {
            Capture request=null;
            try
            {
                var game=GameMain.instance;
                string current=game == null ? "NONE" : game.getScenes().ToString();
                string currentTitle=game==null || game.titleScene==null || TitleState==null ? "UNKNOWN" : TitleState.GetValue(game.titleScene).ToString();
                string currentFade=game==null || game.mapScene==null || MapFade==null ? "UNKNOWN" : MapFade.GetValue(game.mapScene).ToString();
                bool currentLoading=MapScene.IsMapLoading();
                bool currentReady=current=="MAP" && !currentLoading && currentFade=="None"
                    || current=="TITLE" && currentTitle=="TITLE";
                lock(Gate)
                {
                    Guard(); drawSequence++; scene=current; ready=currentReady;
                    titleState=currentTitle; mapFade=currentFade; mapLoading=currentLoading;
                    if (pending != null && completed == null)
                    {
                        request=pending; pending=null;
                        request.InputSnapshot=new Dictionary<string,object> {
                            {"action_id",channel.Input.ActionId},{"held",channel.Input.Held},
                            {"sample_seq",channel.Input.SampleSequence},{"at_ms",GetTickCount64()}};
                    }
                }
                if (request == null) return;
                if (GetTickCount64()>=request.Expiry || game == null || game.gameView == null)
                    throw new InvalidOperationException("capture_expired_or_no_render_view");
                // Read pixels while this render context is current. Encoding has its own bounded worker.
                CapturePhase(request,"readback");
                request.Image=Yukar.Engine.Graphics.getRenderCapture(game.gameView,0,0,Yukar.Engine.Graphics.ScreenWidth,Yukar.Engine.Graphics.ScreenHeight,false);
                request.At=GetTickCount64(); request.DrawSequence=drawSequence; request.Scene=current;
                request.TitleState=currentTitle; request.MapFade=currentFade; request.MapLoading=currentLoading;
                CapturePhase(request,"waiting_encode");
                lock(Gate) { completed=request; }
                CaptureAvailable.Set();
            }
            catch(Exception error) { CaptureError(request,error); if(request != null && request.Image != null) request.Image.Dispose(); Fail("capture_failed"); }
        }
        static void Fail(string reason)
        {
            failure=reason; Volatile.Write(ref ioFailed,1);
            lock(Gate) { if(channel != null) channel.Fault(reason); }
        }
        static Dictionary<string,object> Read(string name)
        {
            string path=Path.Combine(root,name);
            ulong deadline=GetTickCount64()+100;
            while(true)
            {
                try
                {
                    TraceIo("attributes",path);
                    if ((File.GetAttributes(path)&FileAttributes.ReparsePoint)!=0) throw new IOException("Reparse point in channel");
                    TraceIo("read_open",path);
                    using(var file=new FileStream(path,FileMode.Open,FileAccess.Read,FileShare.ReadWrite|FileShare.Delete))
                    {
                        TraceIo("read",path);
                        if(file.Length>65536) throw new IOException("Oversized channel message");
                        using(var reader=new StreamReader(file,Encoding.UTF8,true)) return Json.Deserialize<Dictionary<string,object>>(reader.ReadToEnd());
                    }
                }
                catch(IOException e)
                {
                    int code=e.HResult&0xffff;
                    if((code!=2 && code!=32 && code!=33) || GetTickCount64()>=deadline) throw;
                    Thread.Sleep(5);
                }
            }
        }
        static Dictionary<string,object> Identity()
        {
            return new Dictionary<string,object> {{"protocol",2},{"bridge_version",3},{"session_id",channel.Session},{"pid",channel.Pid},{"creation_id",channel.Creation}};
        }
        static void Publish(string name, object value)
        {
            string target=Path.Combine(root,name), temporary=target+".part";
            // Control and capture publication run concurrently; serializers are not shared between them.
            var serializer=new JavaScriptSerializer { MaxJsonLength=65536, RecursionLimit=8 };
            TraceIo("serialize",target,temporary);
            byte[] bytes=new UTF8Encoding(false).GetBytes(serializer.Serialize(value));
            bool diagnostic=name=="target-status.json";
            if(diagnostic) Phase("status_open");
            TraceIo("open",target,temporary);
            using(var file=new FileStream(temporary,FileMode.Create,FileAccess.Write,FileShare.Read))
            {
                if(diagnostic) Phase("status_write");
                TraceIo("write",target,temporary);
                file.Write(bytes,0,bytes.Length);
                if(diagnostic) Phase("status_flush");
                // Status is an expiring snapshot, not a durable journal. Atomic replacement
                // needs visible bytes, while forcing the device cache can stall control.
                TraceIo("flush",target,temporary);
                file.Flush(!diagnostic && name!="target-io.json");
                TraceIo("close",target,temporary);
            }
            if(diagnostic) Phase("status_replace");
            ulong deadline=GetTickCount64()+100;
            while(true)
            {
                try
                {
                    TraceIo("exists",target,temporary);
                    bool exists=File.Exists(target);
                    TraceIo(exists ? "replace" : "move",target,temporary);
                    if(exists) File.Replace(temporary,target,null); else File.Move(temporary,target);
                    return;
                }
                catch(Exception e)
                {
                    int code=e.HResult&0xffff;
                    if(!(e is IOException || e is UnauthorizedAccessException)
                        || (code!=5 && code!=32 && code!=33 && code!=1175) || GetTickCount64()>=deadline) throw;
                    Thread.Sleep(5);
                }
            }
        }
        static object Evidence(QueryEvidence value)
        {
            if(value == null) return null;
            return new Dictionary<string,object> {{"action_id",value.ActionId},{"sample_seq",value.SampleSequence},{"query_type",value.QueryType},
                {"game_state",value.GameState},{"at_ms",value.At},{"value",value.Value},{"hold_until_ms",value.HoldUntil}};
        }
        static void StatusWorker()
        {
            threadIo=StatusIo;
            while(true)
            {
                try
                {
                    PublishStatus();
                    lock(Gate) { lastStatusIo=GetTickCount64(); }
                    TraceIo("sleep");
                }
                catch(Exception error) { TraceError(error); Fail("io_or_runtime_failure"); }
                Thread.Sleep(10);
            }
        }
        static void PublishSharedStatus(Dictionary<string,object> status)
        {
            TraceIo("shm_write");
            WriteShared(0,status);
        }
        static void PublishStatus()
        {
            Phase("status_snapshot");
            TraceIo("snapshot");
            Dictionary<string,object> status;
            lock(Gate)
            {
                Guard(); status=Identity();
                status.Add("published_at_ms",GetTickCount64()); status.Add("command_seq",channel.Sequence); status.Add("command_kind",channel.Kind);
                status.Add("sample_seq",channel.Input.SampleSequence); status.Add("draw_seq",drawSequence); status.Add("scene",scene); status.Add("ready",ready);
                status.Add("loading_protocol",1); status.Add("title_state",titleState); status.Add("map_loading",mapLoading); status.Add("map_fade",mapFade);
                status.Add("paused",channel.Paused); status.Add("held",channel.Input.Held); status.Add("error",channel.Error);
                status.Add("press",Evidence(channel.Input.PressEvidence)); status.Add("release",Evidence(channel.Input.ReleaseEvidence));
                status.Add("io_phase",ioPhase); status.Add("io_max_phase",ioMaxPhase);
                status.Add("io_max_phase_ms",Interlocked.Read(ref ioMaxPhaseMs));
                status.Add("io_stall_phase",ioStallPhase); status.Add("io_stall_ms",Interlocked.Read(ref ioStallMs));
                status.Add("io_stall_detail",ioStallDetail); status.Add("runtime",RuntimeValue());
                var capture=lastCapture;
                status.Add("capture",capture == null ? null : new Dictionary<string,object> {
                    {"sequence",capture.Sequence},{"id",capture.Id},{"phase",capture.Phase},
                    {"accepted_at_ms",capture.AcceptedAt},{"phase_at_ms",capture.PhaseAt},
                    {"phase_ms",GetTickCount64()-capture.PhaseAt},{"error",capture.Error}});
            }
            PublishSharedStatus(status);
        }
        static void CaptureRequest()
        {
            string capturePath=Path.Combine(root,"target-capture.json");
            if(!File.Exists(capturePath)) return;
            var message=Read("target-capture.json");
            lock(Gate)
            {
                channel.Identity(message,"sequence","id","expires_at_ms");
                ulong sequence=ChannelState.Integer(message["sequence"]);
                if(sequence<=(ulong)captureSequence) return;
                ulong expiry=ChannelState.Integer(message["expires_at_ms"]);
                var id=message["id"] as string;
                ulong now=GetTickCount64();
                if(sequence!=(ulong)captureSequence+1 || !ChannelState.Identifier(id) || expiry<=now || expiry-now>3000 || captureBusy)
                    throw new ArgumentException("Invalid capture request");
                captureSequence=(long)sequence; captureBusy=true;
                pending=new Capture { Sequence=(long)sequence, Id=id, Expiry=expiry,
                    AcceptedAt=now, PhaseAt=now, Phase="waiting_draw" };
                lastCapture=pending;
            }
            TraceIo("delete",capturePath);
            File.Delete(capturePath);
        }
        static void PublishCapture()
        {
            Capture value;
            lock(Gate) { value=completed; if(value == null) return; completed=null; }
            string relative="screenshots/"+value.Sequence.ToString("D8")+".png";
            string target=Path.Combine(root,relative), temporary=target+".part";
            try
            {
                if(GetTickCount64()>=value.Expiry) throw new IOException("Capture expired before encoding");
                CapturePhase(value,"encoding");
                TraceIo("encode",target,temporary);
                value.Image.Save(temporary,ImageFormat.Png);
                CapturePhase(value,"image_publish");
                TraceIo("move",target,temporary);
                File.Move(temporary,target);
                CapturePhase(value,"hashing");
                TraceIo("hash",target);
                string hash;
                using(var sha=SHA256.Create()) using(var file=File.OpenRead(target)) hash=BitConverter.ToString(sha.ComputeHash(file)).Replace("-","").ToLowerInvariant();
                if(GetTickCount64()>=value.Expiry) throw new IOException("Capture expired before publication");
                var receipt=Identity(); receipt.Add("sequence",value.Sequence); receipt.Add("id",value.Id);
                receipt.Add("screenshot",relative); receipt.Add("sha256",hash); receipt.Add("captured_at_ms",value.At);
                receipt.Add("draw_seq",value.DrawSequence); receipt.Add("scene",value.Scene);
                receipt.Add("input_snapshot",value.InputSnapshot);
                receipt.Add("loading_protocol",1); receipt.Add("title_state",value.TitleState);
                receipt.Add("map_loading",value.MapLoading); receipt.Add("map_fade",value.MapFade);
                CapturePhase(value,"receipt_publish");
                Publish("target-capture-result.json",receipt);
                CapturePhase(value,"done");
                lock(Gate) { captureBusy=false; }
            }
            catch(Exception error) { CaptureError(value,error); throw; }
            finally { value.Image.Dispose(); }
        }
        static void CaptureWorker()
        {
            threadIo=CaptureIo;
            try
            {
                while(true)
                { TraceIo("waiting_capture"); CaptureAvailable.WaitOne(); PublishCapture(); }
            }
            catch(Exception error) { TraceError(error); Fail("io_or_runtime_failure"); }
        }
        static void StartupRecord(string phase, string failedPhase, Exception error)
        {
            // Diagnostic publication must never grant control or prevent neutralization.
            try
            {
                var process=Process.GetCurrentProcess();
                string detail=error == null ? null : error.Message;
                if(detail != null && detail.Length>512) detail=detail.Substring(0,512);
                Publish("target-startup.json",new Dictionary<string,object> {
                    {"schema_version",1},{"pid",process.Id},
                    {"creation_id",process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(System.Globalization.CultureInfo.InvariantCulture)},
                    {"published_at_ms",GetTickCount64()},{"phase",phase},{"failed_phase",failedPhase},
                    {"error_type",error == null ? null : error.GetType().Name},
                    {"hresult",error == null ? 0 : error.HResult},{"error",detail}});
            }
            catch(Exception) { }
        }
        static void Worker()
        {
            threadIo=ControlIo;
            string startupPhase="channel_root";
            bool startupComplete=false, safeRoot=false;
            try
            {
                root=Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_BRIDGE");
                if(String.IsNullOrEmpty(root) || !Path.IsPathRooted(root) || !Directory.Exists(root)
                    || (File.GetAttributes(root)&FileAttributes.ReparsePoint)!=0) throw new IOException("Missing channel root");
                safeRoot=true;
                startupPhase="binding_read";
                StartupRecord(startupPhase,null,null);
                var binding=Read("binding.json");
                startupPhase="binding_validate";
                var process=Process.GetCurrentProcess();
                string creation=process.StartTime.ToUniversalTime().ToFileTimeUtc().ToString(System.Globalization.CultureInfo.InvariantCulture);
                var state=new ChannelState(binding["session_id"] as string,process.Id,creation);
                state.Identity(binding);
                startupPhase="channel_directories";
                foreach(string name in new[]{"commands","screenshots"})
                    if(!Directory.Exists(Path.Combine(root,name)) || (File.GetAttributes(Path.Combine(root,name))&FileAttributes.ReparsePoint)!=0) throw new IOException("Unsafe channel subdirectory");
                startupPhase="realtime_ipc";
                OpenSharedMemory(state.Pid,state.Session);
                lock(Gate) { channel=state; lastIo=lastStatusIo=GetTickCount64(); }
                startupPhase="diagnostic_worker";
                var heartbeatThread=new Thread(RuntimeHeartbeat);
                heartbeatThread.IsBackground=true; heartbeatThread.Name="GameQaKit.Bakin.Heartbeat"; heartbeatThread.Start();
                var diagnosticThread=new Thread(DiagnosticWorker);
                diagnosticThread.IsBackground=true; diagnosticThread.Name="GameQaKit.Bakin.Diagnostic"; diagnosticThread.Start();
                startupPhase="capture_worker";
                var captureThread=new Thread(CaptureWorker);
                captureThread.IsBackground=true; captureThread.Name="GameQaKit.Bakin.Capture"; captureThread.Start();
                startupPhase="initial_status";
                PublishStatus();
                lock(Gate) { lastStatusIo=GetTickCount64(); }
                var statusThread=new Thread(StatusWorker);
                statusThread.IsBackground=true; statusThread.Name="GameQaKit.Bakin.Status"; statusThread.Start();
                while(true)
                {
                    Phase("lease_read");
                    TraceIo("shm_lease");
                    var lease=ReadShared(1);
                    if(lease != null)
                    {
                        // Expired heartbeat is normal when the host goes away; it cannot regrant permission.
                        Phase("lease_apply");
                        lock(Gate)
                        {
                            channel.Identity(lease,"expires_at_ms");
                            ulong expiry=ChannelState.Integer(lease["expires_at_ms"]), now=GetTickCount64();
                            if(expiry>now) channel.Lease(lease,now); else channel.Tick(now);
                        }
                    }
                    Phase("command_scan");
                    TraceIo("scan",Path.Combine(root,"commands"));
                    if(Directory.GetFiles(Path.Combine(root,"commands"),"*.json").Length>64) throw new IOException("Command backlog exceeded");
                    string next="commands/"+(channel.Sequence+1).ToString("D20")+".json";
                    if(File.Exists(Path.Combine(root,next)))
                    {
                        Phase("command_read");
                        var command=Read(next);
                        Phase("command_apply");
                        lock(Gate) { channel.Apply(command,GetTickCount64()); }
                        Phase("command_delete");
                        TraceIo("delete",Path.Combine(root,next));
                        File.Delete(Path.Combine(root,next));
                    }
                    Phase("capture_request");
                    CaptureRequest();
                    if(!startupComplete)
                    { StartupRecord("running",null,null); startupComplete=true; }
                    lock(Gate) { lastIo=GetTickCount64(); }
                    Phase("sleep");
                    TraceIo("sleep");
                    Thread.Sleep(10);
                }
            }
            catch(Exception e)
            {
                TraceError(e);
                Fail(e is ArgumentException ? "invalid_channel_message" : "io_or_runtime_failure");
                if(safeRoot && !startupComplete) StartupRecord("failed",startupPhase,e);
                // The independent status publisher reports the latched failure.
                while(channel != null) Thread.Sleep(25);
            }
        }
    }
}
