using System;

namespace GameQaKit.Unity
{
    public interface IInputBackend : IDisposable
    {
        string Name { get; }
        bool Ready { get; }
        int PatchedMethods { get; }
        bool DrivesFrames { get; }
        event Action BeforeUpdate;
        void Advance(float delta);
    }
#if !IL2CPP
    public sealed class LegacyBackend : IInputBackend
    {
        private readonly InputRouter router=new InputRouter();
        public string Name { get { return "legacy_managed_calls"; } }
        public bool Ready { get { return router.PatchedMethods>0; } }
        public int PatchedMethods { get { return router.PatchedMethods; } }
        public bool DrivesFrames { get { return false; } }
        public event Action BeforeUpdate { add {} remove {} }
        public LegacyBackend(string directory) { try { router.Install(directory); } catch { router.Dispose(); throw; } }
        public void Advance(float delta) { LegacyInput.Advance(delta); }
        public void Dispose() { router.Dispose(); }
    }
#endif
}
