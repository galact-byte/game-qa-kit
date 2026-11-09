using System;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
namespace GameQaKit.Unity.Compatibility
{
    public static class OfflineEntry
    {
        private static bool started;
        public static void Start()
        {
            if(started) return;
            started=true;
            string log=Path.Combine(Directory.GetCurrentDirectory(),"gameqa-offline-entry.log");
            try
            {
                AppDomain.CurrentDomain.AssemblyResolve+=Resolve;
                Initialize();
                File.WriteAllText(log,"Chainloader started");
            }
            catch(Exception error) { File.WriteAllText(log,error.ToString()); throw; }
        }
        private static Assembly Resolve(object sender,ResolveEventArgs args)
        {
            string file=Path.Combine(Directory.GetCurrentDirectory(),"BepInEx/core/"+new AssemblyName(args.Name).Name+".dll");
            return File.Exists(file)?Assembly.LoadFrom(file):null;
        }
        [MethodImpl(MethodImplOptions.NoInlining)]
        private static void Initialize()
        {
            string exe=System.Diagnostics.Process.GetCurrentProcess().MainModule.FileName;
            BepInEx.Bootstrap.Chainloader.Initialize(exe,false,null);
            BepInEx.Bootstrap.Chainloader.Start();
        }
    }
}
