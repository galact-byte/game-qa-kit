using System;
using System.IO;
using System.Reflection;

namespace GameQaKit.Unity.Compatibility
{
    public static class DoorstopEntry
    {
        public static void Main()
        {
            string log=Path.Combine(Directory.GetCurrentDirectory(),"gameqa-doorstop-entry.log");
            try
            {
                File.WriteAllText(log,"Main entered");
                string invoked=Environment.GetEnvironmentVariable("DOORSTOP_INVOKE_DLL_PATH");
                string preloader=Path.Combine(Path.GetDirectoryName(Path.GetFullPath(invoked)),"BepInEx.Preloader.dll");
                Environment.SetEnvironmentVariable("DOORSTOP_INVOKE_DLL_PATH",preloader);
                var assembly=Assembly.LoadFrom(preloader);
                assembly.GetType("Doorstop.Entrypoint",true).GetMethod("Start",BindingFlags.Public|BindingFlags.Static).Invoke(null,null);
                File.AppendAllText(log,"\nStart completed");
            }
            catch(Exception error) { File.AppendAllText(log,"\n"+error); throw; }
        }
    }
}
