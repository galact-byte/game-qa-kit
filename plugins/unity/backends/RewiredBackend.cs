using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using HarmonyLib;
using Mono.Cecil;
using Rewired;
using UnityEngine;

namespace GameQaKit.Unity
{
    public sealed class RewiredBackend : IInputBackend
    {
        private readonly ControlCore core;
        private readonly Harmony harmony=new Harmony("org.gameqakit.unity.rewired");
        private readonly InputRouter legacy=new InputRouter();
        private readonly Dictionary<Player,Dictionary<int,List<ActionBinding>>> cache=new Dictionary<Player,Dictionary<int,List<ActionBinding>>>();
        private static readonly HashSet<string> supported=new HashSet<string>(new[]{"GetAxis","GetAxisRaw","GetButton","GetButtonDown","GetButtonUp","GetNegativeButton","GetNegativeButtonDown","GetNegativeButtonUp","GetAnyButton","GetAnyButtonDown","GetAnyButtonUp","GetAnyNegativeButton","GetAnyNegativeButtonDown","GetAnyNegativeButtonUp"});
        private static RewiredBackend current;
        private readonly Dictionary<UnityEngine.EventSystems.BaseInputModule,UnityEngine.EventSystems.BaseInput> uiInputs=new Dictionary<UnityEngine.EventSystems.BaseInputModule,UnityEngine.EventSystems.BaseInput>();
        private static readonly FieldInfo uiOverride=typeof(UnityEngine.EventSystems.BaseInputModule).GetField("m_InputOverride",BindingFlags.Instance|BindingFlags.NonPublic);
        private BridgeUiInput uiInput;
        private bool disposed;
        public event Action BeforeUpdate { add {} remove {} }
        public string Name { get { return "rewired_player_maps"; } }
        public bool Ready { get { return !disposed && ReInput.isReady; } }
        public int PatchedMethods { get; private set; }
        public bool DrivesFrames { get { return false; } }
        public RewiredBackend(ControlCore control,string managedDirectory)
        {
            core=control;
            try
            {
                Audit(managedDirectory);
                current=this;
                foreach(var method in typeof(Player).GetMethods(BindingFlags.Public|BindingFlags.Instance))
                {
                    if(method.Name=="AddInputEventDelegate")
                    {
                        harmony.Patch(method,prefix:new HarmonyMethod(typeof(RewiredBackend),"UnsupportedEvent"));
                        continue;
                    }
                    if(!supported.Contains(method.Name)) continue;
                    var parameters=method.GetParameters();
                    if(parameters.Length>1 || (parameters.Length==1 && parameters[0].ParameterType!=typeof(int) && parameters[0].ParameterType!=typeof(string))) continue;
                    harmony.Patch(method,prefix:new HarmonyMethod(typeof(RewiredBackend),method.ReturnType==typeof(float) ? "AxisRead" : "ButtonRead"));
                    PatchedMethods++;
                }
                if(PatchedMethods==0) throw new NotSupportedException("No supported Rewired.Player input getters");
                legacy.Install(managedDirectory,true);
                harmony.Patch(typeof(Rewired.Mouse).GetProperty("screenPosition").GetGetMethod(),
                    prefix:new HarmonyMethod(typeof(RewiredBackend),"MousePosition"));
                foreach(string name in new[]{"GetButton","GetButtonDown","GetButtonUp"})
                {
                    var method=typeof(Rewired.Controller).GetMethod(name,new[]{typeof(int)});
                    if(method==null) throw new NotSupportedException("Missing Rewired mouse query: "+name);
                    harmony.Patch(method,prefix:new HarmonyMethod(typeof(RewiredBackend),"MouseButton"));
                }
                PatchedMethods+=legacy.PatchedMethods+4;
            }
            catch { Dispose(); throw; }
        }
        private static void Audit(string directory)
        {
            foreach(var assembly in AppDomain.CurrentDomain.GetAssemblies())
            {
                if(assembly is System.Reflection.Emit.AssemblyBuilder || string.IsNullOrEmpty(assembly.Location) || assembly.GetName().Name.StartsWith("Rewired")) continue;
                if(!Path.GetFullPath(assembly.Location).StartsWith(Path.GetFullPath(directory)+Path.DirectorySeparatorChar,StringComparison.OrdinalIgnoreCase)) continue;
                using(var definition=AssemblyDefinition.ReadAssembly(assembly.Location))
                    foreach(var type in Types(definition.MainModule.Types)) foreach(var method in type.Methods)
                    {
                        if(!method.HasBody) continue;
                        foreach(var instruction in method.Body.Instructions)
                        {
                            var call=instruction.Operand as MethodReference;
                            if(call==null || call.DeclaringType.FullName!="Rewired.Player") continue;
                            if((call.Name.Contains("Button") || call.Name.Contains("Axis")) && call.Name.StartsWith("Get") &&
                                (!supported.Contains(call.Name) || call.Parameters.Count>1))
                                throw new NotSupportedException("Unmapped Rewired input query: "+call.FullName);
                        }
                    }
            }
        }
        private static IEnumerable<TypeDefinition> Types(IEnumerable<TypeDefinition> types)
        {
            foreach(var type in types) { yield return type; foreach(var nested in Types(type.NestedTypes)) yield return nested; }
        }
        private Dictionary<int,List<ActionBinding>> Bindings(Player player)
        {
            Dictionary<int,List<ActionBinding>> actions;
            if(cache.TryGetValue(player,out actions)) return actions;
            actions=new Dictionary<int,List<ActionBinding>>();
            foreach(var map in player.controllers.maps.GetAllMaps(ControllerType.Keyboard))
            {
                if(!map.enabled) continue;
                foreach(var element in map.AllMaps)
                {
                    string token=Token(element.keyCode);
                    if(token==null) continue;
                    List<ActionBinding> bindings;
                    if(!actions.TryGetValue(element.actionId,out bindings)) actions[element.actionId]=bindings=new List<ActionBinding>();
                    bindings.Add(new ActionBinding(token,element.axisContribution==Pole.Negative ? -1 : 1));
                }
            }
            cache[player]=actions;
            return actions;
        }
        private IEnumerable<ActionBinding> For(Player player,object[] args)
        {
            if(!Ready) return new ActionBinding[0];
            var actions=Bindings(player);
            if(args.Length==0) return actions.Values.SelectMany(items=>items);
            int id=args[0] is int ? (int)args[0] : ReInput.mapping.GetActionId((string)args[0]);
            List<ActionBinding> bindings;
            return actions.TryGetValue(id,out bindings) ? bindings : (IEnumerable<ActionBinding>)new ActionBinding[0];
        }
        private static bool MousePosition(ref Vector2 __result)
        {
            __result=LegacyInput.get_mousePosition();
            return false;
        }
        private static bool MouseButton(object __instance,int __0,MethodBase __originalMethod,ref bool __result)
        {
            if(!(__instance is Rewired.Mouse)) return true;
            string name=__originalMethod.Name;
            __result=name.EndsWith("Down") ? LegacyInput.GetMouseButtonDown(__0) :
                name.EndsWith("Up") ? LegacyInput.GetMouseButtonUp(__0) : LegacyInput.GetMouseButton(__0);
            return false;
        }
        private static bool UnsupportedEvent()
        {
            if(current!=null) current.core.Fail("rewired_event_subscription_not_supported");
            return false;
        }
        private static bool AxisRead(Player __instance,object[] __args,ref float __result)
        {
            __result=0;
            if(current!=null)
            {
                try { __result=ActionState.Axis(current.core,current.For(__instance,__args)); }
                catch(Exception error) { current.core.Fail("rewired_mapping_failed: "+error.Message); }
            }
            return false;
        }
        private static bool ButtonRead(Player __instance,object[] __args,MethodBase __originalMethod,ref bool __result)
        {
            __result=false;
            if(current!=null)
            {
                try
                {
                    string name=__originalMethod.Name;
                    int polarity=name.Contains("Negative") ? -1 : name.Contains("Any") ? 0 : 1;
                    int edge=name.EndsWith("Down") ? 1 : name.EndsWith("Up") ? 2 : 0;
                    __result=ActionState.Button(current.core,current.For(__instance,__args),polarity,edge);
                }
                catch(Exception error) { current.core.Fail("rewired_mapping_failed: "+error.Message); }
            }
            return false;
        }
        private static string Token(KeyCode key)
        {
            switch(key)
            {
                case KeyCode.UpArrow: case KeyCode.W: return "up";
                case KeyCode.DownArrow: case KeyCode.S: return "down";
                case KeyCode.LeftArrow: case KeyCode.A: return "left";
                case KeyCode.RightArrow: case KeyCode.D: return "right";
                case KeyCode.Return: case KeyCode.Space: case KeyCode.Z: return "ok";
                case KeyCode.Escape: case KeyCode.Backspace: case KeyCode.X: return "cancel";
                case KeyCode.LeftShift: case KeyCode.RightShift: return "shift";
                case KeyCode.PageUp: return "pageup";
                case KeyCode.PageDown: return "pagedown";
                default: return null;
            }
        }
        public void Advance(float delta)
        {
            cache.Clear(); LegacyInput.Advance(delta);
            var system=UnityEngine.EventSystems.EventSystem.current;
            if(system==null) return;
            if(uiInput==null) uiInput=system.gameObject.AddComponent<BridgeUiInput>();
            foreach(var module in system.GetComponents<UnityEngine.EventSystems.BaseInputModule>())
            {
                if(uiOverride==null) throw new NotSupportedException("UI input override is unavailable");
                if(!uiInputs.ContainsKey(module)) uiInputs[module]=(UnityEngine.EventSystems.BaseInput)uiOverride.GetValue(module);
                uiOverride.SetValue(module,uiInput);
            }
        }
        public void Dispose()
        {
            if(disposed) return;
            disposed=true;
            foreach(var item in uiInputs) if(item.Key!=null) uiOverride.SetValue(item.Key,item.Value);
            uiInputs.Clear();
            if(uiInput!=null) UnityEngine.Object.Destroy(uiInput);
            harmony.UnpatchSelf(); legacy.Dispose(); cache.Clear();
            if(current==this) current=null;
        }
    }
}
