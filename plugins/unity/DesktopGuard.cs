using System;
using System.Runtime.InteropServices;
using System.Text;

namespace GameQaKit.Unity
{
    public static class DesktopGuard
    {
        [DllImport("kernel32.dll")]
        private static extern uint GetCurrentThreadId();
        [DllImport("user32.dll")]
        private static extern IntPtr GetThreadDesktop(uint threadId);
        [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
        private static extern bool GetUserObjectInformation(IntPtr handle, int index,
            StringBuilder name, uint length, out uint required);
        public static bool Matches(string expected)
        {
            if(string.IsNullOrEmpty(expected) || string.Equals(expected,"Default",StringComparison.OrdinalIgnoreCase)) return false;
            var name=new StringBuilder(256);
            uint required;
            return GetUserObjectInformation(GetThreadDesktop(GetCurrentThreadId()),2,name,512,out required) &&
                string.Equals(expected,name.ToString(),StringComparison.OrdinalIgnoreCase);
        }
    }
}
