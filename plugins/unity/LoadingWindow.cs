using System;
using System.Collections.Generic;

namespace GameQaKit.Unity
{
    // 不读取 Unity 对象；场景入口、完成通知与实际渲染帧由桥分别提供。
    public sealed class LoadingWindow
    {
        public const double LimitSeconds=10;
        public string Id { get; private set; }
        public double StartedAt { get; private set; }
        public long StartFrame { get; private set; }
        public long StartObservationSequence { get; private set; }
        public long ReleaseFrame { get; private set; }
        public long CompletionFrame { get; private set; }
        public bool Eligible { get; private set; }
        public bool Recovered { get; private set; }
        public bool Active { get { return Id!=null && !Recovered; } }
        private readonly HashSet<string> requested=new HashSet<string>(StringComparer.Ordinal);
        private readonly HashSet<string> pending=new HashSet<string>(StringComparer.Ordinal);
        public string[] Requested { get { var items=new string[requested.Count]; requested.CopyTo(items); return items; } }
        public string[] Pending { get { var items=new string[pending.Count]; pending.CopyTo(items); return items; } }
        public void Begin(string scene,ControlCore core,double now,long sequence)
        {
            if(!Active)
            {
                Id=Guid.NewGuid().ToString("N"); StartedAt=now; StartFrame=core.Frame;
                StartObservationSequence=sequence; ReleaseFrame=core.ReleaseFrame;
                Eligible=core.ReleaseConfirmed && core.HeldCount==0 && ControlCore.Finite(now);
                Recovered=false; requested.Clear(); pending.Clear();
            }
            if(string.IsNullOrEmpty(scene) || !requested.Add(scene)) Eligible=false;
            else pending.Add(scene);
            Eligible=Eligible && core.ReleaseConfirmed && core.ReleaseFrame==ReleaseFrame && core.HeldCount==0;
            CompletionFrame=0;
            core.Fail(Eligible ? "scene_loading" : "loading_release_unconfirmed");
        }
        public void Loaded(string scene,long frame)
        {
            if(Active && pending.Remove(scene) && pending.Count==0) CompletionFrame=frame;
        }
        public static bool ImageUsable(int width,int height,Func<int,int> rgb)
        {
            if(width<=2 || height<=2 || (long)width*height>16777216) return false;
            int total=(width-2)*(height-2),count=0,black=0,white=0;
            for(int index=0;index<total;index+=Math.Max(1,total/20000))
            {
                int value=rgb((height-2-index/(width-2))*width+index%(width-2)+1);
                if(value==0) black++;
                if(value==0xffffff) white++;
                count++;
            }
            return Math.Max(black,white)/(double)count<.999;
        }
        public bool Observe(long captureFrame,ControlCore core,double now)
        {
            if(!Active || !Eligible || pending.Count!=0 || CompletionFrame<=0 ||
                captureFrame<=CompletionFrame || now-StartedAt>=LimitSeconds || now<StartedAt ||
                !core.Paused || core.Active!=null || core.HeldCount!=0 || !core.ReleaseConfirmed ||
                core.ReleaseFrame!=ReleaseFrame) return false;
            Recovered=true;
            return true;
        }
    }
}
