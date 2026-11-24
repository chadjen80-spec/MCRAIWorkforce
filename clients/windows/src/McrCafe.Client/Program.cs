// MCR Smart Café seat client.
//
//   mcr-cafe-client register --server https://cafe.example.com --code 3F2A-91BC-07DE [--name PC-12]
//   mcr-cafe-client run      [--once-seconds N]       connect, heartbeat, run signed commands, show remaining time
//   mcr-cafe-client status                            seat and remaining time
//   mcr-cafe-client help "message"                    call a staff member to this seat
//   mcr-cafe-client print <file> [--copies N]         hold a document for staff (printing module not built yet)
//   mcr-cafe-client apps                              the local allowlist and install status
//
// Global option: --data-dir <folder> (default %ProgramData%\MCR\SmartCafe).
using McrCafe.Client.Core;

const string Version = "0.1.0";

var argList = args.ToList();
string? Opt(string name)
{
    var i = argList.IndexOf(name);
    if (i < 0 || i + 1 >= argList.Count)
        return null;
    var v = argList[i + 1];
    argList.RemoveRange(i, 2);
    return v;
}

var paths = new ClientPaths(Opt("--data-dir") ?? ClientPaths.DefaultDataDir);
var store = new DeviceStore(paths);
var command = argList.FirstOrDefault() ?? "usage";

try
{
    switch (command)
    {
        case "register":
        {
            var server = Opt("--server") ?? throw new ArgumentException("--server is required");
            var code = Opt("--code") ?? throw new ArgumentException("--code is required");
            var name = Opt("--name") ?? Environment.MachineName;
            using var api = new CafeApiClient(server);
            var reg = await api.RegisterAsync(code, name, Version);
            store.Save(new DeviceIdentity(api.Server.ToString(), reg.DeviceId, reg.OrganizationId, reg.SeatLabel), reg.DeviceSecret, reg.ServerPublicKey);
            if (!File.Exists(paths.Allowlist))
                File.WriteAllText(paths.Allowlist, "{\n  \"apps\": []\n}\n");
            Console.WriteLine($"Registered as seat {reg.SeatLabel} (device {reg.DeviceId}).");
            Console.WriteLine($"Server key pinned. Add approved programs to {paths.Allowlist}.");
            return 0;
        }
        case "run":
        {
            var onceSeconds = Opt("--once-seconds");
            var (id, api) = Connect();
            using var cts = onceSeconds is null ? new CancellationTokenSource() : new CancellationTokenSource(TimeSpan.FromSeconds(int.Parse(onceSeconds)));
            Console.CancelKeyPress += (_, e) => { e.Cancel = true; cts.Cancel(); };
            var agent = new DeviceAgent(store, api, Version, msg => Console.WriteLine($"{DateTime.Now:HH:mm:ss} {msg}"));
            Console.WriteLine($"MCR Smart Café client {Version} — seat {id.SeatLabel}, server {id.Server}");
            var display = Task.Run(async () =>
            {
                while (!cts.IsCancellationRequested)
                {
                    var left = TimeSpan.FromSeconds(agent.RemainingSecondsNow());
                    var title = $"Seat {id.SeatLabel} | {(agent.Status?.SessionActive == true ? $"Time left {left:h\\:mm\\:ss}" : "No active session")} | {(agent.Connected ? "online" : "offline")}";
                    if (!Console.IsOutputRedirected)
                        Console.Title = title;
                    await Task.Delay(1000, cts.Token).ContinueWith(_ => { });
                }
            });
            var lastShown = "";
            var printer = Task.Run(async () =>
            {
                while (!cts.IsCancellationRequested)
                {
                    await Task.Delay(5000, cts.Token).ContinueWith(_ => { });
                    var line = agent.Status is null ? "" : $"seat {id.SeatLabel}: {(agent.Status.SessionActive ? $"{TimeSpan.FromSeconds(agent.RemainingSecondsNow()):h\\:mm\\:ss} left" : "no active session")}";
                    if (line != "" && line[..^3] != (lastShown.Length > 3 ? lastShown[..^3] : lastShown))
                        Console.WriteLine($"{DateTime.Now:HH:mm:ss} {line}");
                    lastShown = line;
                }
            });
            await agent.RunAsync(cts.Token);
            await Task.WhenAll(display, printer);
            return 0;
        }
        case "status":
        {
            var (id, api) = Connect();
            var s = await api.StatusAsync();
            Console.WriteLine(s.SessionActive ? $"Seat {s.SeatLabel}: {TimeSpan.FromSeconds(s.RemainingSeconds):h\\:mm\\:ss} left" : $"Seat {s.SeatLabel}: no active session");
            return 0;
        }
        case "help":
        {
            var (_, api) = Connect();
            var r = await api.AssistAsync(argList.ElementAtOrDefault(1) ?? "");
            Console.WriteLine($"Staff called (request {r.GetProperty("id").GetString()}).");
            return 0;
        }
        case "print":
        {
            var file = argList.ElementAtOrDefault(1) ?? throw new ArgumentException("print <file>");
            var copies = int.Parse(Opt("--copies") ?? "1");
            var req = new HeldPrintQueue(paths.PrintQueue).Submit(file, copies, color: false);
            Console.WriteLine($"Held for staff release: {req.FileName} x{req.Copies} (not sent to a printer; printing module not built yet).");
            return 0;
        }
        case "apps":
        {
            var list = Allowlist.Load(paths.Allowlist);
            foreach (var (appId, state) in list.InstallStatus())
                Console.WriteLine($"{appId,-20} {state}");
            if (list.Entries.Count == 0)
                Console.WriteLine($"No approved programs. Edit {paths.Allowlist}.");
            return 0;
        }
        default:
            Console.WriteLine("usage: mcr-cafe-client [--data-dir DIR] register|run|status|help|print|apps (see Program.cs header)");
            return command == "usage" ? 0 : 2;
    }
}
catch (Exception ex) when (ex is CafeApiException or ArgumentException or AllowlistException or HttpRequestException or FileNotFoundException or InvalidOperationException)
{
    Console.Error.WriteLine($"error: {ex.Message}");
    return 1;
}

(DeviceIdentity, CafeApiClient) Connect()
{
    if (!store.IsRegistered)
        throw new InvalidOperationException("This PC is not registered. Run: mcr-cafe-client register --server URL --code CODE");
    var id = store.LoadIdentity();
    var api = new CafeApiClient(id.Server);
    api.TokenAsync(id.DeviceId, store.LoadSecret()).GetAwaiter().GetResult();
    return (id, api);
}
