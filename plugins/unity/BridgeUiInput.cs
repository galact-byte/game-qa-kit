using UnityEngine;
using UnityEngine.EventSystems;

namespace GameQaKit.Unity
{
    public sealed class BridgeUiInput : BaseInput
    {
        public override bool mousePresent { get { return LegacyInput.get_mousePresent(); } }
        public override Vector2 mousePosition { get { return LegacyInput.get_mousePosition(); } }
        public override Vector2 mouseScrollDelta { get { return LegacyInput.get_mouseScrollDelta(); } }
        public override bool GetMouseButtonDown(int button) { return LegacyInput.GetMouseButtonDown(button); }
        public override bool GetMouseButtonUp(int button) { return LegacyInput.GetMouseButtonUp(button); }
        public override bool GetMouseButton(int button) { return LegacyInput.GetMouseButton(button); }
        public override bool touchSupported { get { return LegacyInput.get_touchSupported(); } }
        public override int touchCount { get { return LegacyInput.get_touchCount(); } }
        public override Touch GetTouch(int index) { return LegacyInput.GetTouch(index); }
        public override float GetAxisRaw(string axis) { return LegacyInput.GetAxisRaw(axis); }
        public override bool GetButtonDown(string button) { return LegacyInput.GetButtonDown(button); }
    }
}
