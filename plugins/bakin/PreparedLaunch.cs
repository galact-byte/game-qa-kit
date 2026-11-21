using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Web.Script.Serialization;

namespace GameQaKit.Bakin
{
    public static class PreparedLaunch
    {
        public static Process Record(string executable, string arguments)
        {
            string root = Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_PREPARE");
            string session = Environment.GetEnvironmentVariable("GAMEQAKIT_BAKIN_SESSION");
            if (String.IsNullOrEmpty(root) || String.IsNullOrEmpty(session))
                throw new InvalidOperationException("Preparation binding is missing");
            var process = Process.GetCurrentProcess();
            var receipt = new { protocol = 2, phase = "prepared", session_id = session,
                pid = process.Id, creation_id = process.StartTime.ToFileTimeUtc().ToString(),
                executable = executable, arguments = arguments };
            string output = Path.Combine(root, "prepared.json");
            string temporary = output + ".part";
            if (File.Exists(output) || File.Exists(temporary))
                throw new InvalidOperationException("Preparation receipt already exists");
            File.WriteAllText(temporary, new JavaScriptSerializer().Serialize(receipt), new UTF8Encoding(false));
            File.Move(temporary, output);
            return null;
        }
    }
}
