using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Threading;

namespace Yukar.Engine
{
    public static class Input
    {
        public enum StateType { DIRECT, TRIGGER, TRIGGER_UP, REPEAT }
        public enum GameState { WALK, MENU, SYSTEM }
        public enum KeyStates : ulong { NONE=0, UP=1, DOWN=2, RIGHT=4, LEFT=8, DECIDE=16, CANCEL=32 }
        public static bool SuppressCursorControl;
    }
    public sealed class TitleScene
    {
        internal enum State { FADEIN, TITLE }
        internal State state = State.FADEIN;
    }
    public sealed class MapScene
    {
        enum FadeStatus { None, FadeIn }
        private FadeStatus fadeStatus = FadeStatus.None;
        public static bool Loading;
        public static bool IsMapLoading() { return Loading; }
        public void SetFade(bool fading) { fadeStatus=fading ? FadeStatus.FadeIn : FadeStatus.None; }
    }
    public sealed class GameMain
    {
        public static GameMain instance = new GameMain();
        public object gameView = new object();
        public TitleScene titleScene = new TitleScene();
        public MapScene mapScene = new MapScene();
        public string Scene = "TITLE";
        public string getScenes() { return Scene; }
    }
    public static class Graphics
    {
        public static int ScreenWidth=640, ScreenHeight=360;
        public static Bitmap getRenderCapture(object view,int x,int y,int w,int h,bool alpha)
        {
            string root=Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_BRIDGE");
            while(File.Exists(Path.Combine(root,"fixture.block-readback")) && !File.Exists(Path.Combine(root,"fixture.stop")))
                Thread.Sleep(5);
            var b = new Bitmap(w,h);
            if(File.Exists(Path.Combine(Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_BRIDGE"),"fixture.noisy-capture")))
            {
                b.Dispose(); b=new Bitmap(4096,4096,System.Drawing.Imaging.PixelFormat.Format32bppArgb);
                var pixels=new byte[4096*4096*4]; new Random(42).NextBytes(pixels);
                var data=b.LockBits(new Rectangle(0,0,b.Width,b.Height),System.Drawing.Imaging.ImageLockMode.WriteOnly,b.PixelFormat);
                try { Marshal.Copy(pixels,0,data.Scan0,pixels.Length); } finally { b.UnlockBits(data); }
            }
            else using(var g = System.Drawing.Graphics.FromImage(b)) { g.Clear(Color.CornflowerBlue); g.FillRectangle(Brushes.Gold,15,20,100,100); }
            return b;
        }
    }
}
class RuntimeFixture
{
    static int Main(string[] args)
    {
        string root=args[0];
        File.WriteAllText(Path.Combine(root,"fixture.pid"),System.Diagnostics.Process.GetCurrentProcess().Id.ToString());
        while(!File.Exists(Path.Combine(root,"binding.json"))) Thread.Sleep(5);
        try
        {
            while(!File.Exists(Path.Combine(root,"fixture.stop")))
            {
                var game=Yukar.Engine.GameMain.instance;
                game.Scene=File.Exists(Path.Combine(root,"fixture.logo")) && !File.Exists(Path.Combine(root,"fixture.fadein")) ? "LOGO" : "TITLE";
                game.titleScene.state=File.Exists(Path.Combine(root,"fixture.ready")) ? Yukar.Engine.TitleScene.State.TITLE : Yukar.Engine.TitleScene.State.FADEIN;
                if(File.Exists(Path.Combine(root,"fixture.map"))) game.Scene="MAP";
                Yukar.Engine.MapScene.Loading=File.Exists(Path.Combine(root,"fixture.map-loading"));
                game.mapScene.SetFade(File.Exists(Path.Combine(root,"fixture.map-fading")));
                if(File.Exists(Path.Combine(root,"fixture.block-input")))
                {
                    File.WriteAllText(Path.Combine(root,"fixture.input-blocked"), "1");
                    while(File.Exists(Path.Combine(root,"fixture.block-input")) && !File.Exists(Path.Combine(root,"fixture.stop"))) Thread.Sleep(5);
                    File.Delete(Path.Combine(root,"fixture.input-blocked"));
                }
                GameQaKit.Bakin.RuntimeBridge.BeforeInput();
                GameQaKit.Bakin.RuntimeBridge.AfterInput();
                var held = GameQaKit.Bakin.RuntimeBridge.GetState(Yukar.Engine.Input.StateType.DIRECT,Yukar.Engine.Input.GameState.WALK);
                GameQaKit.Bakin.RuntimeBridge.GetState(Yukar.Engine.Input.StateType.TRIGGER,Yukar.Engine.Input.GameState.WALK);
                if(!File.Exists(Path.Combine(root,"fixture.skip-draw"))) GameQaKit.Bakin.RuntimeBridge.AfterDraw();
                File.WriteAllText(Path.Combine(root,"fixture.held"),((ulong)held).ToString());
                Thread.Sleep(10);
            }
            return 0;
        }
        catch(Exception e) { Console.Error.WriteLine(e); return 1; }
    }
}
