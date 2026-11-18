using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;

namespace GameQa.Emuera
{
    internal static class Launcher
    {
        const string RuntimeHash = "a9af3d1e8b7eb4b9323faf6da6d770addf507e4fd2ee0eb5cfc2e58603fa58e6";
        internal static string DirectoryPath;
        internal static string Session;
        internal static Assembly Engine;
        internal static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = 65536 };
        internal static readonly int Pid = Process.GetCurrentProcess().Id;

        [STAThread]
        static int Main()
        {
            try
            {
                DirectoryPath = Environment.GetEnvironmentVariable("GAMEQAKIT_BRIDGE_DIR");
                Session = Environment.GetEnvironmentVariable("GAMEQAKIT_SESSION_ID");
                string runtime = Environment.GetEnvironmentVariable("GAMEQAKIT_ENGINE");
                if (!Path.IsPathRooted(DirectoryPath ?? "") || String.IsNullOrEmpty(Session)
                    || !Path.IsPathRooted(runtime ?? "") || Path.GetDirectoryName(runtime) != AppDomain.CurrentDomain.BaseDirectory.TrimEnd('\\'))
                    throw new InvalidOperationException("Explicit local runtime and session required");
                using (var sha = SHA256.Create())
                using (var stream = File.OpenRead(runtime))
                    if (BitConverter.ToString(sha.ComputeHash(stream)).Replace("-", "").ToLowerInvariant() != RuntimeHash)
                        throw new InvalidOperationException("Unvalidated runtime digest");
                Engine = Assembly.LoadFrom(runtime);
                // 原入口负责 WinForms 默认值，观察器不能提前创建控件。
                Application.Idle += Install;
                Engine.EntryPoint.Invoke(null, new object[] { new string[0] });
                return 0;
            }
            catch (Exception error)
            {
                if (!String.IsNullOrEmpty(DirectoryPath))
                    File.WriteAllText(Path.Combine(DirectoryPath, "launcher-error.txt"), error.ToString());
                return 3;
            }
        }

        static void Install(object sender, EventArgs args)
        {
            Application.Idle -= Install;
            new Observer().Start();
        }

        internal static Dictionary<string, object> Read(string name)
        {
            string path = Path.Combine(DirectoryPath, name + ".json");
            var elapsed = Stopwatch.StartNew();
            for (;;)
            {
                try
                {
                    using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
                    using (var reader = new StreamReader(stream))
                    {
                        if (stream.Length > 65536) throw new InvalidDataException("Control file too large");
                        return Json.Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    }
                }
                catch (FileNotFoundException) { return new Dictionary<string, object>(); }
                catch (IOException) { if (elapsed.ElapsedMilliseconds >= 100) throw; }
                catch (UnauthorizedAccessException) { if (elapsed.ElapsedMilliseconds >= 100) throw; }
                Thread.Sleep(5);
            }
        }

        internal static void Publish(string name, object value)
        {
            string path = Path.Combine(DirectoryPath, name + ".json"), temp = path + ".part";
            File.WriteAllText(temp, Json.Serialize(value));
            var elapsed = Stopwatch.StartNew();
            for (;;)
            {
                try
                {
                    if (File.Exists(path)) File.Replace(temp, path, null);
                    else File.Move(temp, path);
                    return;
                }
                catch (IOException) { if (elapsed.ElapsedMilliseconds >= 100) throw; }
                catch (UnauthorizedAccessException) { if (elapsed.ElapsedMilliseconds >= 100) throw; }
                Thread.Sleep(5);
            }
        }
    }

    internal sealed class Observer
    {
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 20 };
        readonly List<object> events = new List<object>();
        Form form;
        RichTextBox input;
        bool ready;
        long seq;
        string pendingId = "", pendingKind = "", lastCommand = "";
        bool held;
#if GAMEQA_TESTING
        bool masked;
        void Mask(string colour)
        {
            if (masked) return;
            masked = true;
            form.FormBorderStyle = FormBorderStyle.None;
            var panel = new Panel { Dock = DockStyle.Fill,
                BackColor = colour == "white" ? System.Drawing.Color.White : System.Drawing.Color.Black };
            form.Controls.Add(panel);
            foreach (Control original in form.Controls)
                if (original != panel) original.Hide();
            panel.BringToFront();
            if (colour == "speck")
                panel.Controls.Add(new Panel { Left = 30, Top = 30, Width = 1, Height = 1, BackColor = System.Drawing.Color.White });
            panel.Refresh();
        }
#endif
        [DllImport("user32.dll", SetLastError = true)]
        static extern bool PostMessage(IntPtr hwnd, uint message, IntPtr wparam, IntPtr lparam);

        internal void Start()
        {
            timer.Tick += Tick;
            timer.Start();
        }

        static IEnumerable<Control> Descendants(Control root)
        {
            foreach (Control child in root.Controls)
            {
                yield return child;
                foreach (Control nested in Descendants(child)) yield return nested;
            }
        }

        bool Bind()
        {
            foreach (Form candidate in Application.OpenForms)
            {
                if (candidate.GetType().FullName != "MinorShift.Emuera.MainWindow" || !candidate.Visible) continue;
                foreach (Control child in Descendants(candidate))
                {
                    var edit = child as RichTextBox;
                    if (edit == null) continue;
                    if (input != null) throw new InvalidOperationException("Ambiguous input control");
                    input = edit;
                }
                form = candidate;
                if (input == null) throw new InvalidOperationException("Input control absent");
                return true;
            }
            return false;
        }

        bool AtInitialWait()
        {
            // Idle 可早于原窗口的初始化定时器。等固定夹具进入 WAIT，再发布就绪。
            const BindingFlags members = BindingFlags.Instance | BindingFlags.NonPublic;
            var console = form.GetType().GetField("console", members).GetValue(form);
            if (console == null) return false;
            var type = console.GetType();
            return type.GetField("state", members).GetValue(console).ToString() == "WaitInput"
                && (bool)type.GetProperty("IsWaitingEnterKey", members).GetValue(console, null);
        }

        void AppendObservers()
        {
            input.KeyDown -= Down;
            input.KeyUp -= Up;
            input.KeyDown += Down;
            input.KeyUp += Up;
            // 原引擎会重新挂载处理器；每次发送前核对实际委托顺序，不能依赖首次订阅顺序。
            var key = typeof(Control).GetField("EventKeyDown", BindingFlags.NonPublic | BindingFlags.Static);
            var handlers = (EventHandlerList)typeof(Component).GetProperty("Events", BindingFlags.NonPublic | BindingFlags.Instance).GetValue(input, null);
            if (key == null) throw new InvalidOperationException("Unknown WinForms event layout");
            Delegate chain = handlers[key.GetValue(null)];
            Delegate[] calls = chain == null ? new Delegate[0] : chain.GetInvocationList();
            if (calls.Length != 2 || calls[0].Method.Name != "richTextBox1_KeyDown"
                || calls[0].Target != form || calls[1].Method != new KeyEventHandler(Down).Method)
                throw new InvalidOperationException("Original handler must precede the observer");
        }

        void Down(object sender, KeyEventArgs args) { Consumed("down", args); }
        void Up(object sender, KeyEventArgs args) { Consumed("up", args); }

        void Consumed(string kind, KeyEventArgs args)
        {
            if (args.KeyCode != Keys.Return || pendingKind != kind || pendingId.Length == 0) return;
#if GAMEQA_TESTING
            var mode = Launcher.Read("test-mode");
            object fault;
            if (mode.TryGetValue("action", out fault) && (string)fault == "block-" + kind)
                Thread.Sleep(Timeout.Infinite);
#endif
            held = kind == "down";
            events.Add(new { id = pendingId, kind = kind, after_original_handler = true, seq = seq + 1 });
            if (events.Count > 128) events.RemoveAt(0);
            pendingId = pendingKind = "";
#if GAMEQA_TESTING
            if (kind == "up" && mode.TryGetValue("action", out fault) && (string)fault == "blank-after") Mask("black");
#endif
            Publish();
        }

        void Tick(object sender, EventArgs args)
        {
            try
            {
                if (form == null && !Bind()) return;
                if (!ready)
                {
                    if (!AtInitialWait()) return;
                    ready = true;
                }
                // 嵌套 DoEvents 不能将尚未返回的输入处理器伪装成活跃、已消费。
                if (pendingId.Length != 0) return;
#if GAMEQA_TESTING
                var mode = Launcher.Read("test-mode");
                object fault;
                if (mode.TryGetValue("action", out fault) && (string)fault == "hang-ui")
                    Thread.Sleep(Timeout.Infinite);
                object blank;
                if (mode.TryGetValue("blank", out blank)) Mask((string)blank);
#endif
                var command = Launcher.Read("input");
                object id, session, pid, kind;
                if (command.TryGetValue("id", out id) && (string)id != lastCommand
                    && command.TryGetValue("session_id", out session) && (string)session == Launcher.Session
                    && command.TryGetValue("pid", out pid) && Convert.ToInt32(pid) == Launcher.Pid
                    && command.TryGetValue("kind", out kind))
                {
                    string action = (string)kind;
                    if (action != "down" && action != "up" && action != "close")
                        throw new InvalidOperationException("Unsupported observer command");
                    lastCommand = (string)id;
                    if (action == "close")
                    {
                        if (held) throw new InvalidOperationException("Close requires released input");
                        if (!PostMessage(form.Handle, 0x0010, IntPtr.Zero, IntPtr.Zero))
                            throw new System.ComponentModel.Win32Exception();
                    }
                    else
                    {
                        AppendObservers();
                        pendingId = (string)id;
                        pendingKind = action;
                        long bits = 1 | (0x1c << 16);
                        if (action == "up") bits |= (3L << 30);
                        if (!PostMessage(input.Handle, action == "down" ? 0x0100U : 0x0101U,
                                         new IntPtr(13), new IntPtr(unchecked((int)bits))))
                            throw new System.ComponentModel.Win32Exception();
                        return;
                    }
                }
                Publish();
            }
            catch (Exception error)
            {
                timer.Stop();
                File.WriteAllText(Path.Combine(Launcher.DirectoryPath, "observer-error.txt"), error.ToString());
            }
        }

        void Publish()
        {
            var program = Launcher.Engine.GetType("MinorShift.Emuera.Program", true);
            Launcher.Publish("observer", new {
                session_id = Launcher.Session, pid = Launcher.Pid, seq = ++seq, ready = true,
                hwnd = form.Handle.ToInt64(), input_hwnd = input.Handle.ToInt64(), held = held,
                application_path = Application.ExecutablePath,
                engine_directory = (string)program.GetProperty("ExeDir").GetValue(null, null),
                engine_name = (string)program.GetProperty("ExeName").GetValue(null, null), events = events
            });
        }
    }
}
