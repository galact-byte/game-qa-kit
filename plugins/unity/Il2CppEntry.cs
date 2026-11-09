#if IL2CPP
using BepInEx;
using BepInEx.Logging;
using BepInEx.Unity.IL2CPP;

namespace GameQaKit.Unity
{
    [BepInPlugin("org.gameqakit.unity.bridge","Game QA Unity Bridge","0.1.0")]
    public sealed class Il2CppEntry : BasePlugin
    {
        internal static ManualLogSource RuntimeLog;
        public override void Load()
        {
            RuntimeLog=Log;
            AddComponent<GameQaBridge>();
        }
    }
}
#endif
