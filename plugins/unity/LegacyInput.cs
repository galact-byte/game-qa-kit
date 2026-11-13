using System;
using System.Collections.Generic;
using UnityEngine;
using NativeInput=UnityEngine.Input;

namespace GameQaKit.Unity
{
    // Only managed game call sites are redirected. Native Unity input remains available in manual mode.
    public static class LegacyInput
    {
        public static ControlCore Core;
        private static Vector3 pointer;
        private static float horizontal,vertical;
        public static float Sensitivity=3f, Gravity=3f;
        private static bool Ai { get { return Core!=null && !Core.Paused; } }
        public static void Advance(float delta)
        {
            if(!Ai) { horizontal=vertical=0; return; }
            horizontal=Smooth(horizontal,Raw("left","right"),delta);
            vertical=Smooth(vertical,Raw("down","up"),delta);
            if(Core.Active!=null && Core.Active.Action=="click")
                pointer=new Vector3(Core.Active.X,Screen.height-1-Core.Active.Y,0);
        }
        private static float Smooth(float value,float target,float delta)
        {
            if(value*target<0) value=0;
            return Mathf.MoveTowards(value,target,Mathf.Max(0,delta)*(target==0 ? Gravity : Sensitivity));
        }
        private static float Raw(string negative,string positive)
        {
            return (Core.Held(positive)?1:0)-(Core.Held(negative)?1:0);
        }
        private static string Token(KeyCode key)
        {
            switch(key)
            {
                case KeyCode.UpArrow: case KeyCode.W: return "up";
                case KeyCode.DownArrow: case KeyCode.S: return "down";
                case KeyCode.LeftArrow: case KeyCode.A: return "left";
                case KeyCode.RightArrow: case KeyCode.D: return "right";
                case KeyCode.Return: case KeyCode.KeypadEnter: case KeyCode.Space: case KeyCode.Z: return "ok";
                case KeyCode.Escape: case KeyCode.X: case KeyCode.Backspace: return "cancel";
                case KeyCode.LeftShift: case KeyCode.RightShift: return "shift";
                case KeyCode.PageUp: return "pageup";
                case KeyCode.PageDown: return "pagedown";
                case KeyCode.Mouse0: return "mouse0";
                default: return null;
            }
        }
        private static string Token(string name)
        {
            if(name==null) return null;
            switch(name.ToLowerInvariant())
            {
                case "up": case "w": return "up";
                case "down": case "s": return "down";
                case "left": case "a": return "left";
                case "right": case "d": return "right";
                case "return": case "enter": case "space": case "z": case "submit": case "jump": return "ok";
                case "escape": case "x": case "backspace": case "cancel": return "cancel";
                case "left shift": case "right shift": case "shift": return "shift";
                case "page up": return "pageup";
                case "page down": return "pagedown";
                case "mouse 0": return "mouse0";
                default: return null;
            }
        }
        private static bool Read(string token,int edge)
        {
            if(token==null) return false;
            Core.Observe(token);
            if(edge==0 || (edge==2 && Core.Up(token))) Core.ObserveReleased(token);
            return edge==1 ? Core.Down(token) : edge==2 ? Core.Up(token) : Core.Held(token);
        }
        public static bool GetKey(KeyCode key) { return Ai ? Read(Token(key),0) : NativeInput.GetKey(key); }
        public static bool GetKeyDown(KeyCode key) { return Ai ? Read(Token(key),1) : NativeInput.GetKeyDown(key); }
        public static bool GetKeyUp(KeyCode key) { return Ai ? Read(Token(key),2) : NativeInput.GetKeyUp(key); }
        public static bool GetKey(string key) { return Ai ? Read(Token(key),0) : NativeInput.GetKey(key); }
        public static bool GetKeyDown(string key) { return Ai ? Read(Token(key),1) : NativeInput.GetKeyDown(key); }
        public static bool GetKeyUp(string key) { return Ai ? Read(Token(key),2) : NativeInput.GetKeyUp(key); }
        public static bool GetButton(string name) { return Ai ? Read(Token(name),0) : NativeInput.GetButton(name); }
        public static bool GetButtonDown(string name) { return Ai ? Read(Token(name),1) : NativeInput.GetButtonDown(name); }
        public static bool GetButtonUp(string name) { return Ai ? Read(Token(name),2) : NativeInput.GetButtonUp(name); }
        private static float Axis(string name,bool raw)
        {
            if(name=="Horizontal")
            { Core.Observe("left"); Core.Observe("right");
                float value=raw ? Raw("left","right") : horizontal;
                if(value==0) { Core.ObserveReleased("left"); Core.ObserveReleased("right"); }
                return value; }
            if(name=="Vertical")
            { Core.Observe("down"); Core.Observe("up");
                float value=raw ? Raw("down","up") : vertical;
                if(value==0) { Core.ObserveReleased("down"); Core.ObserveReleased("up"); }
                return value; }
            return 0;
        }
        public static float GetAxis(string name) { return Ai ? Axis(name,false) : NativeInput.GetAxis(name); }
        public static float GetAxisRaw(string name) { return Ai ? Axis(name,true) : NativeInput.GetAxisRaw(name); }
        public static bool GetMouseButton(int button) { return Ai ? Read(button==0 ? "mouse0" : null,0) : NativeInput.GetMouseButton(button); }
        public static bool GetMouseButtonDown(int button) { return Ai ? Read(button==0 ? "mouse0" : null,1) : NativeInput.GetMouseButtonDown(button); }
        public static bool GetMouseButtonUp(int button) { return Ai ? Read(button==0 ? "mouse0" : null,2) : NativeInput.GetMouseButtonUp(button); }
        public static Vector3 get_mousePosition() { return Ai ? pointer : NativeInput.mousePosition; }
        public static Vector2 get_mouseScrollDelta() { return Ai ? Vector2.zero : NativeInput.mouseScrollDelta; }
        public static bool get_mousePresent() { return Ai || NativeInput.mousePresent; }
        public static Vector3 get_acceleration() { return Ai ? Vector3.zero : NativeInput.acceleration; }
        public static int get_touchCount() { return Ai ? 0 : NativeInput.touchCount; }
        public static Touch[] get_touches() { return Ai ? new Touch[0] : NativeInput.touches; }
        public static Touch GetTouch(int index) { if(Ai) throw new IndexOutOfRangeException("No touch device in legacy bridge"); return NativeInput.GetTouch(index); }
        public static bool get_touchSupported() { return !Ai && NativeInput.touchSupported; }
        public static bool get_simulateMouseWithTouches() { return !Ai && NativeInput.simulateMouseWithTouches; }
        public static void set_simulateMouseWithTouches(bool value) { NativeInput.simulateMouseWithTouches=value; }
        public static string get_compositionString() { return Ai ? "" : NativeInput.compositionString; }
        public static IMECompositionMode get_imeCompositionMode() { return Ai ? IMECompositionMode.Off : NativeInput.imeCompositionMode; }
        public static void set_imeCompositionMode(IMECompositionMode value) { if(!Ai) NativeInput.imeCompositionMode=value; }
        public static Vector2 get_compositionCursorPos() { return Ai ? Vector2.zero : NativeInput.compositionCursorPos; }
        public static void set_compositionCursorPos(Vector2 value) { if(!Ai) NativeInput.compositionCursorPos=value; }
        public static string[] GetJoystickNames() { return Ai ? new string[0] : NativeInput.GetJoystickNames(); }
        public static void ResetInputAxes() { if(Ai) Core.Fail("game_input_reset"); else NativeInput.ResetInputAxes(); }
        public static string get_inputString() { return Ai ? "" : NativeInput.inputString; }
        public static bool get_anyKey()
        {
            if(!Ai) return NativeInput.anyKey;
            foreach(string key in Core.HeldButtons) Core.Observe(key);
            return Core.HeldCount>0;
        }
        public static bool get_anyKeyDown()
        {
            if(!Ai) return NativeInput.anyKeyDown;
            foreach(string key in Core.HeldButtons) if(Core.Down(key)) { Core.Observe(key); return true; }
            return false;
        }
    }
}
