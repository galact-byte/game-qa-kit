#if !IL2CPP
using System;
using System.Reflection;
using HarmonyLib;
using UnityEngine.SceneManagement;

namespace GameQaKit.Unity
{
    // 仅拦截已核实的字符串同步加载入口；未知入口仍走原失联回收规则。
    internal sealed class SceneLoadingHook : IDisposable
    {
        private static Action<string> starting;
        private readonly Harmony harmony=new Harmony("org.gameqakit.unity.loading");
        private readonly MethodInfo method;
        internal SceneLoadingHook(Action<string> callback)
        {
            if(starting!=null) throw new InvalidOperationException("Scene loading hook already installed");
            method=typeof(SceneManager).GetMethod("LoadScene",new[]{typeof(string),typeof(LoadSceneMode)});
            if(method==null) throw new MissingMethodException("SceneManager.LoadScene(string, LoadSceneMode)");
            starting=callback;
            try { harmony.Patch(method,prefix:new HarmonyMethod(typeof(SceneLoadingHook).GetMethod("Before",BindingFlags.NonPublic|BindingFlags.Static))); }
            catch { starting=null; throw; }
        }
        private static void Before(string __0) { if(starting!=null) starting(__0); }
        public void Dispose() { harmony.UnpatchSelf(); starting=null; }
    }
}
#endif
