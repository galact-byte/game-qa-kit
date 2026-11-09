using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using HarmonyLib;
using UnityEngine;
using UnityEngine.InputSystem;
using UnityEngine.InputSystem.Controls;
using UnityEngine.InputSystem.LowLevel;

namespace GameQaKit.Unity
{
    public sealed class InputSystemBackend : IInputBackend
    {
        private readonly ControlCore core;
        private readonly Harmony harmony=new Harmony("org.gameqakit.unity.inputsystem");
        private readonly List<InputDevice> disabled=new List<InputDevice>();
        private readonly InputSettings.BackgroundBehavior background;
        private readonly InputSettings.UpdateMode updateMode;
        private Keyboard keyboard;
        private Mouse mouse;
        private static InputSystemBackend current;
        private bool disposed;
        private Vector2 pointer;
#if IL2CPP
        private Il2CppSystem.Action beforeCallback;
        private Il2CppSystem.Action<Il2CppSystem.Object,InputActionChange> actionCallback;
        private Il2CppSystem.Action<InputDevice,InputDeviceChange> deviceCallback;
#endif
        public event Action BeforeUpdate;
        public string Name { get { return "input_system_virtual_devices"; } }
        public bool Ready { get { return !disposed && keyboard!=null && mouse!=null; } }
        public int PatchedMethods { get; private set; }
        public bool DrivesFrames { get { return true; } }
        public InputSystemBackend(ControlCore control)
        {
            core=control;
            background=InputSystem.settings.backgroundBehavior;
            updateMode=InputSystem.settings.updateMode;
            try
            {
                current=this;
                Debug.Log("GameQa InputSystem: configuring background updates");
                InputSystem.settings.backgroundBehavior=InputSettings.BackgroundBehavior.IgnoreFocus;
                InputSystem.settings.updateMode=InputSettings.UpdateMode.ProcessEventsInDynamicUpdate;
                Debug.Log("GameQa InputSystem: registering virtual layouts");
#if IL2CPP
                var matcher=new Il2CppSystem.Nullable<UnityEngine.InputSystem.Layouts.InputDeviceMatcher>();
                InputSystem.RegisterLayout("{\"name\":\"GameQaKeyboard\",\"extend\":\"Keyboard\",\"canRunInBackground\":true}","GameQaKeyboard",matcher);
                InputSystem.RegisterLayout("{\"name\":\"GameQaMouse\",\"extend\":\"Mouse\",\"canRunInBackground\":true}","GameQaMouse",matcher);
#else
                InputSystem.RegisterLayout("{\"name\":\"GameQaKeyboard\",\"extend\":\"Keyboard\",\"canRunInBackground\":true}");
                InputSystem.RegisterLayout("{\"name\":\"GameQaMouse\",\"extend\":\"Mouse\",\"canRunInBackground\":true}");
#endif
                Debug.Log("GameQa InputSystem: creating virtual devices");
#if IL2CPP
                keyboard=InputSystem.AddDevice("GameQaKeyboard").Cast<Keyboard>();
                mouse=InputSystem.AddDevice("GameQaMouse").Cast<Mouse>();
#else
                keyboard=(Keyboard)InputSystem.AddDevice("GameQaKeyboard");
                mouse=(Mouse)InputSystem.AddDevice("GameQaMouse");
#endif
                foreach(var device in InputSystem.devices) Isolate(device);
                keyboard.MakeCurrent(); mouse.MakeCurrent();
                Debug.Log("GameQa InputSystem: installing consumption probes");
                foreach(string name in new[]{"isPressed","wasPressedThisFrame","wasReleasedThisFrame"})
                {
                    var method=AccessTools.PropertyGetter(typeof(ButtonControl),name);
                    if(method==null) throw new NotSupportedException("Missing InputSystem ButtonControl."+name);
                    harmony.Patch(method,postfix:new HarmonyMethod(typeof(InputSystemBackend),"ButtonRead"));
                    PatchedMethods++;
                }
                Debug.Log("GameQa InputSystem: subscribing update callbacks");
#if IL2CPP
                beforeCallback=(Action)BeforeInput;
                actionCallback=(Action<Il2CppSystem.Object,InputActionChange>)ActionChanged;
                deviceCallback=(Action<InputDevice,InputDeviceChange>)DeviceChanged;
                InputSystem.add_onActionChange(actionCallback);
                InputSystem.add_onDeviceChange(deviceCallback);
                InputSystem.add_onBeforeUpdate(beforeCallback);
#else
                InputSystem.onActionChange+=ActionChanged;
                InputSystem.onDeviceChange+=DeviceChanged;
                InputSystem.onBeforeUpdate+=BeforeInput;
#endif
                Debug.Log("GameQa InputSystem: ready");
            }
            catch { Dispose(); throw; }
        }
        private bool Owns(InputDevice device)
        {
#if IL2CPP
            return device!=null && ((keyboard!=null && device.Pointer==keyboard.Pointer) || (mouse!=null && device.Pointer==mouse.Pointer));
#else
            return device==keyboard || device==mouse;
#endif
        }
        private void Isolate(InputDevice device)
        {
            if(!Owns(device) && device.enabled)
            { disabled.Add(device); InputSystem.DisableDevice(device); }
        }
        private void DeviceChanged(InputDevice device,InputDeviceChange change)
        {
            if(disposed || Owns(device)) return;
            if(change==InputDeviceChange.Added || change==InputDeviceChange.Enabled || change==InputDeviceChange.Reconnected)
            {
                Isolate(device);
                keyboard.MakeCurrent(); mouse.MakeCurrent();
            }
        }
        private static void ButtonRead(ButtonControl __instance)
        { if(current!=null) current.Observe(__instance); }
#if IL2CPP
        private void ActionChanged(Il2CppSystem.Object item,InputActionChange change)
#else
        private void ActionChanged(object item,InputActionChange change)
#endif
        {
            if(change!=InputActionChange.ActionPerformed) return;
#if IL2CPP
            var action=item.TryCast<InputAction>();
#else
            var action=item as InputAction;
#endif
            if(action!=null && action.activeControl!=null) Observe(action.activeControl);
        }
        private void Observe(InputControl control)
        {
            if(core.Paused || !Owns(control.device)) return;
            string name=control.name;
#if IL2CPP
            if(control.device.Pointer==mouse.Pointer) { if(name=="leftButton") core.Observe("mouse0"); return; }
#else
            if(control.device==mouse) { if(name=="leftButton") core.Observe("mouse0"); return; }
#endif
            switch(name)
            {
                case "upArrow": case "w": core.Observe("up"); break;
                case "downArrow": case "s": core.Observe("down"); break;
                case "leftArrow": case "a": core.Observe("left"); break;
                case "rightArrow": case "d": core.Observe("right"); break;
                case "enter": case "space": case "z": core.Observe("ok"); break;
                case "escape": case "backspace": case "x": core.Observe("cancel"); break;
                case "leftShift": core.Observe("shift"); break;
                case "pageUp": core.Observe("pageup"); break;
                case "pageDown": core.Observe("pagedown"); break;
            }
        }
        private void BeforeInput()
        {
            if(disposed || InputState.currentUpdateType!=InputUpdateType.Dynamic) return;
            try
            {
                if(BeforeUpdate!=null) BeforeUpdate();
                var keys=new List<Key>();
                if(!core.Paused)
                {
                    if(core.Held("up")) { keys.Add(Key.UpArrow); keys.Add(Key.W); }
                    if(core.Held("down")) { keys.Add(Key.DownArrow); keys.Add(Key.S); }
                    if(core.Held("left")) { keys.Add(Key.LeftArrow); keys.Add(Key.A); }
                    if(core.Held("right")) { keys.Add(Key.RightArrow); keys.Add(Key.D); }
                    if(core.Held("ok")) keys.Add(Key.Enter);
                    if(core.Held("cancel")) keys.Add(Key.Escape);
                    if(core.Held("shift")) keys.Add(Key.LeftShift);
                    if(core.Held("pageup")) keys.Add(Key.PageUp);
                    if(core.Held("pagedown")) keys.Add(Key.PageDown);
                    if(core.Active!=null && core.Active.Action=="click") pointer=new Vector2(core.Active.X,Screen.height-1-core.Active.Y);
                }
                QueueState(keyboard,keys);
                QueueState(mouse,null);
            }
            catch(Exception error)
            {
                core.Fail("input_system_failed: "+error.Message);
                InputSystem.ResetDevice(keyboard); InputSystem.ResetDevice(mouse);
            }
        }
        private unsafe void QueueState(InputDevice device,List<Key> keys)
        {
            InputEventPtr eventPtr;
            var allocation=StateEvent.FromDefaultStateFor(device,out eventPtr);
            try
            {
                // Use a native state event; IL2CPP need not instantiate QueueStateEvent<TState>.
                IntPtr payload=new IntPtr((byte*)eventPtr.data+24);
                uint size=eventPtr.sizeInBytes-24;
                if(keys!=null)
                {
                    foreach(var key in keys) WriteBit(payload,size,device,keyboard[key]);
                }
                else
                {
                    WriteFloat(payload,size,device,mouse.position.x,pointer.x);
                    WriteFloat(payload,size,device,mouse.position.y,pointer.y);
                    if(!core.Paused && core.Held("mouse0")) WriteBit(payload,size,device,mouse.leftButton);
                }
                InputSystem.QueueEvent(eventPtr);
            }
            finally { allocation.Dispose(); }
        }
        private static uint Offset(InputDevice device,InputControl control,uint size,uint width)
        {
            uint offset=control.stateBlock.byteOffset-device.stateBlock.byteOffset;
            if(offset>size || width>size-offset) throw new InvalidOperationException("Input state lies outside device payload");
            return offset;
        }
        private static void WriteBit(IntPtr payload,uint size,InputDevice device,ButtonControl control)
        {
            if(control.stateBlock.sizeInBits!=1)
                throw new NotSupportedException("Unsupported button state layout");
            uint bitOffset=control.stateBlock.bitOffset;
            uint offsetInDevice=Offset(device,control,size,1)+bitOffset/8;
            if(offsetInDevice>=size) throw new InvalidOperationException("Input bit lies outside device payload");
            int offset=checked((int)offsetInDevice);
            byte value=Marshal.ReadByte(payload,offset);
            Marshal.WriteByte(payload,offset,(byte)(value | (1<<checked((int)(bitOffset%8)))));
        }
        private static void WriteFloat(IntPtr payload,uint size,InputDevice device,AxisControl control,float value)
        {
            if(control.stateBlock.sizeInBits!=32 || control.stateBlock.bitOffset!=0)
                throw new NotSupportedException("Unsupported pointer state layout");
            int offset=checked((int)Offset(device,control,size,4));
            byte[] bytes=BitConverter.GetBytes(value);
            Marshal.Copy(bytes,0,IntPtr.Add(payload,offset),4);
        }
        public void Advance(float delta) {}
        public void Dispose()
        {
            if(disposed) return;
            disposed=true;
#if IL2CPP
            if(beforeCallback!=null) InputSystem.remove_onBeforeUpdate(beforeCallback);
            if(actionCallback!=null) InputSystem.remove_onActionChange(actionCallback);
            if(deviceCallback!=null) InputSystem.remove_onDeviceChange(deviceCallback);
#else
            InputSystem.onBeforeUpdate-=BeforeInput;
            InputSystem.onActionChange-=ActionChanged;
            InputSystem.onDeviceChange-=DeviceChanged;
#endif
            harmony.UnpatchSelf();
            if(keyboard!=null) InputSystem.RemoveDevice(keyboard);
            if(mouse!=null) InputSystem.RemoveDevice(mouse);
            foreach(var device in disabled) if(device.added) InputSystem.EnableDevice(device);
            InputSystem.settings.backgroundBehavior=background;
            InputSystem.settings.updateMode=updateMode;
            if(current==this) current=null;
        }
    }
}
