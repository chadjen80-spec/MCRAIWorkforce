using System.Diagnostics;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using McrCafe.Client.Core;

namespace McrCafe.Client.Tests;

internal sealed class FixedTime(DateTimeOffset now) : TimeProvider
{
    public DateTimeOffset Now { get; set; } = now;
    public override DateTimeOffset GetUtcNow() => Now;
}

internal sealed class RecordingStarter : IProcessStarter
{
    public List<ProcessStartInfo> Started { get; } = [];

    public int Start(ProcessStartInfo info)
    {
        Started.Add(info);
        return 4242;
    }
}

/// <summary>Plays the server: signs envelopes the same way apps/api app.cafe.signing does.</summary>
internal sealed class TestServer
{
    public ECDsa Key { get; } = ECDsa.Create(ECCurve.NamedCurves.nistP256);
    public Guid DeviceId { get; } = Guid.NewGuid();
    public Guid OrgId { get; } = Guid.NewGuid();
    public DateTimeOffset Now { get; } = DateTimeOffset.FromUnixTimeSeconds(1_764_000_000);

    public string PublicPem => Key.ExportSubjectPublicKeyInfoPem();

    public (string Envelope, string Signature) Sign(string type = "launch_app", object? args = null, Guid? device = null, Guid? commandId = null,
                                                    long? issued = null, long? expires = null, ECDsa? key = null)
    {
        var iat = issued ?? Now.ToUnixTimeSeconds();
        var env = JsonSerializer.Serialize(new SortedDictionary<string, object?>(StringComparer.Ordinal)
        {
            ["v"] = 1, ["command_id"] = (commandId ?? Guid.NewGuid()).ToString(), ["org_id"] = OrgId.ToString(),
            ["device_id"] = (device ?? DeviceId).ToString(), ["seat_label"] = "12", ["type"] = type,
            ["args"] = args ?? new { app_id = "demo-game" }, ["issued_at"] = iat, ["expires_at"] = expires ?? iat + 60, ["nonce"] = "n",
        });
        var sig = (key ?? Key).SignData(Encoding.UTF8.GetBytes(env), HashAlgorithmName.SHA256, DSASignatureFormat.IeeeP1363FixedFieldConcatenation);
        return (env, Convert.ToBase64String(sig).TrimEnd('=').Replace('+', '-').Replace('/', '_'));
    }
}

public sealed class CommandSecurityTests : IDisposable
{
    private readonly string _dir = Directory.CreateTempSubdirectory("mcr-cafe-tests-").FullName;
    private readonly TestServer _server = new();
    private readonly RecordingStarter _starter = new();
    private readonly FixedTime _time;
    private readonly string _gamePath;
    private Allowlist _allowlist;

    public CommandSecurityTests()
    {
        _time = new FixedTime(_server.Now);
        _gamePath = Path.Combine(_dir, "demo-game.exe");
        File.WriteAllBytes(_gamePath, [0x4d, 0x5a]);
        _allowlist = new Allowlist([new AllowlistEntry("demo-game", "Demo Game", _gamePath, ["--fullscreen"])]);
    }

    public void Dispose() => Directory.Delete(_dir, true);

    private CommandProcessor Processor(ReplayCache? replay = null)
    {
        var verifier = new CommandVerifier(_server.PublicPem, _server.DeviceId, _server.OrgId, replay ?? new ReplayCache(time: _time), () => _allowlist, _time);
        return new CommandProcessor(verifier, () => _allowlist, new AppLauncher(_starter));
    }

    [Fact]
    public void Valid_signed_command_launches_only_the_allowlisted_path_without_a_shell()
    {
        var (env, sig) = _server.Sign();
        var (id, result) = Processor().Process(env, sig);
        Assert.NotNull(id);
        Assert.Equal(new CommandResult(true, "STARTED", "", 4242, "demo-game"), result);
        var info = Assert.Single(_starter.Started);
        Assert.Equal(_gamePath, info.FileName);
        Assert.False(info.UseShellExecute);
        Assert.Equal(["--fullscreen"], info.ArgumentList);
    }

    [Fact]
    public void Tampered_envelope_is_refused()
    {
        var (env, sig) = _server.Sign();
        var (_, result) = Processor().Process(env.Replace("demo-game", "other-game"), sig);
        Assert.Equal(Refusal.SignatureInvalid, result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Command_signed_by_another_key_is_refused()
    {
        using var attacker = ECDsa.Create(ECCurve.NamedCurves.nistP256);
        var (env, sig) = _server.Sign(key: attacker);
        Assert.Equal(Refusal.SignatureInvalid, Processor().Process(env, sig).Result.Code);
        Assert.Equal(Refusal.SignatureInvalid, Processor().Process(env, "not base64!").Result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Command_for_another_device_is_refused()
    {
        var (env, sig) = _server.Sign(device: Guid.NewGuid());
        Assert.Equal(Refusal.WrongDevice, Processor().Process(env, sig).Result.Code);
    }

    [Fact]
    public void Expired_and_future_commands_are_refused()
    {
        var p = Processor();
        var t = _server.Now.ToUnixTimeSeconds();
        var (env, sig) = _server.Sign(issued: t - 200, expires: t - 100);
        Assert.Equal(Refusal.Expired, p.Process(env, sig).Result.Code);
        (env, sig) = _server.Sign(issued: t + 120, expires: t + 180);
        Assert.Equal(Refusal.Expired, p.Process(env, sig).Result.Code);
        (env, sig) = _server.Sign(issued: t, expires: t + 3600); // a long-lived command is not accepted either
        Assert.Equal(Refusal.Expired, p.Process(env, sig).Result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Replayed_command_runs_once_even_after_a_restart()
    {
        var file = Path.Combine(_dir, "replay.json");
        var (env, sig) = _server.Sign();
        Assert.Equal("STARTED", Processor(new ReplayCache(file, _time)).Process(env, sig).Result.Code);
        Assert.Equal(Refusal.Replayed, Processor(new ReplayCache(file, _time)).Process(env, sig).Result.Code);
        Assert.Single(_starter.Started);
    }

    [Theory]
    [InlineData("exec")]
    [InlineData("shell")]
    [InlineData("run")]
    [InlineData("download")]
    public void Unknown_command_types_are_refused_even_when_signed(string type)
    {
        var (env, sig) = _server.Sign(type, new { command = "cmd.exe /c whoami" });
        Assert.Equal(Refusal.UnknownType, Processor().Process(env, sig).Result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Server_cannot_add_a_path_or_arguments()
    {
        var (env, sig) = _server.Sign(args: new { app_id = "demo-game", path = @"C:\Windows\System32\cmd.exe" });
        Assert.Equal(Refusal.BadArgs, Processor().Process(env, sig).Result.Code);
        (env, sig) = _server.Sign(args: new { app_id = @"..\..\cmd" });
        Assert.Equal(Refusal.BadArgs, Processor().Process(env, sig).Result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void App_not_on_local_allowlist_is_refused()
    {
        var (env, sig) = _server.Sign(args: new { app_id = "steam" });
        Assert.Equal(Refusal.NotAllowlisted, Processor().Process(env, sig).Result.Code);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Missing_program_reports_not_installed()
    {
        _allowlist = new Allowlist([new AllowlistEntry("demo-game", "Demo Game", Path.Combine(_dir, "gone.exe"))]);
        var (env, sig) = _server.Sign();
        Assert.Equal("NOT_INSTALLED", Processor().Process(env, sig).Result.Code);
        Assert.Equal("missing", _allowlist.InstallStatus()["demo-game"]);
    }

    [Fact]
    public void Program_needing_admin_rights_is_reported_clearly_without_a_uac_prompt()
    {
        var verifier = new CommandVerifier(_server.PublicPem, _server.DeviceId, _server.OrgId, new ReplayCache(time: _time), () => _allowlist, _time);
        var launcher = new AppLauncher(new ThrowingStarter(new System.ComponentModel.Win32Exception(740)));
        var (env, sig) = _server.Sign();
        var result = new CommandProcessor(verifier, () => _allowlist, launcher).Process(env, sig).Result;
        Assert.Equal("NEEDS_ELEVATION", result.Code);
        Assert.False(result.Ok);
        Assert.Contains("administrator", result.Message);
        // any other start failure keeps its own code
        var other = new AppLauncher(new ThrowingStarter(new System.ComponentModel.Win32Exception(2))).Launch(_allowlist.Entries.Single());
        Assert.Equal("START_FAILED", other.Code);
    }

    private sealed class ThrowingStarter(Exception ex) : IProcessStarter
    {
        public int Start(ProcessStartInfo info) => throw ex;
    }

    [Fact]
    public void Ping_answers_pong_without_launching()
    {
        var (env, sig) = _server.Sign("ping", new { });
        Assert.Equal(new CommandResult(true, "PONG"), Processor().Process(env, sig).Result);
        Assert.Empty(_starter.Started);
    }

    [Fact]
    public void Verifies_a_command_signed_by_the_python_server()
    {
        var vector = JsonDocument.Parse(File.ReadAllText(Path.Combine(AppContext.BaseDirectory, "Fixtures", "python_vector.json"))).RootElement;
        var env = vector.GetProperty("envelope").GetString()!;
        var sig = vector.GetProperty("signature").GetString()!;
        var parsed = JsonDocument.Parse(env).RootElement;
        var time = new FixedTime(DateTimeOffset.FromUnixTimeSeconds(parsed.GetProperty("issued_at").GetInt64() + 5));
        var verifier = new CommandVerifier(vector.GetProperty("public_key").GetString()!, Guid.Parse(parsed.GetProperty("device_id").GetString()!),
                                           Guid.Parse(parsed.GetProperty("org_id").GetString()!), new ReplayCache(time: time), () => _allowlist, time);
        var cmd = verifier.Verify(env, sig);
        Assert.Equal(("launch_app", "demo-game"), (cmd.Type, cmd.AppId));
        var tampered = new CommandVerifier(vector.GetProperty("public_key").GetString()!, Guid.Parse(parsed.GetProperty("device_id").GetString()!),
                                           Guid.Parse(parsed.GetProperty("org_id").GetString()!), new ReplayCache(time: time), () => _allowlist, time);
        Assert.Equal(Refusal.SignatureInvalid,
                     Assert.Throws<CommandRejectedException>(() => tampered.Verify(env.Replace("\"12\"", "\"13\""), sig)).Code);
    }
}

public sealed class AllowlistTests
{
    [Theory]
    [InlineData(@"C:\Windows\System32\cmd.exe")]
    [InlineData(@"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe")]
    [InlineData(@"C:\Windows\System32\mshta.exe")]
    [InlineData(@"C:\Games\start.bat")]
    [InlineData(@"C:\Games\run.ps1")]
    [InlineData(@"games\relative.exe")]
    [InlineData(@"\\fileserver\share\game.exe")]
    public void Shells_scripts_relative_and_network_paths_are_rejected(string path)
    {
        Assert.Throws<AllowlistException>(() => new Allowlist([new AllowlistEntry("x", "X", path)]));
    }

    [Theory]
    [InlineData("Steam")]
    [InlineData("../steam")]
    [InlineData("")]
    public void Ids_must_be_simple(string id)
    {
        Assert.Throws<AllowlistException>(() => new Allowlist([new AllowlistEntry(id, "X", @"C:\Games\x.exe")]));
    }

    [Fact]
    public void Loads_file_and_reports_install_status()
    {
        var dir = Directory.CreateTempSubdirectory().FullName;
        try
        {
            var exe = Path.Combine(dir, "g.exe");
            File.WriteAllText(exe, "");
            var file = Path.Combine(dir, "allowlist.json");
            File.WriteAllText(file, JsonSerializer.Serialize(new { apps = new object[]
            {
                new { id = "game-a", name = "A", path = exe },
                new { id = "game-b", name = "B", path = Path.Combine(dir, "missing.exe") },
            } }));
            var list = Allowlist.Load(file);
            Assert.Equal(new Dictionary<string, string> { ["game-a"] = "installed", ["game-b"] = "missing" }, list.InstallStatus());
            Assert.Empty(Allowlist.Load(Path.Combine(dir, "none.json")).Entries);
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }

    [Fact]
    public void Real_process_start_reports_pid()
    {
        var hostname = Path.Combine(Environment.SystemDirectory, "hostname.exe");
        Assert.True(File.Exists(hostname));
        var result = new AppLauncher().Launch(new AllowlistEntry("hostname", "Hostname", hostname));
        Assert.True(result.Ok);
        Assert.True(result.Pid > 0);
    }
}

public sealed class ClientPlumbingTests
{
    [Theory]
    [InlineData("http://example.com")]
    [InlineData("ftp://localhost")]
    public void Plain_http_is_only_allowed_to_localhost(string server)
    {
        Assert.Throws<ArgumentException>(() => CafeApiClient.RequireSecure(server));
    }

    [Fact]
    public void WebSocket_uri_follows_the_scheme()
    {
        using var local = new CafeApiClient("http://127.0.0.1:8000");
        Assert.Equal("ws://127.0.0.1:8000/v1/cafe/device/ws", local.WebSocketUri.ToString());
        using var prod = new CafeApiClient("https://cafe.example.com/");
        Assert.Equal("wss://cafe.example.com/v1/cafe/device/ws", prod.WebSocketUri.ToString());
    }

    [Fact]
    public void Device_secret_is_encrypted_at_rest()
    {
        var dir = Directory.CreateTempSubdirectory().FullName;
        try
        {
            var store = new DeviceStore(new ClientPaths(dir));
            store.Save(new DeviceIdentity("http://127.0.0.1:8000/", Guid.NewGuid(), Guid.NewGuid(), "12"), "mcd_super-secret-value", "PEM");
            Assert.DoesNotContain("super-secret", Encoding.UTF8.GetString(File.ReadAllBytes(store.Paths.Secret)));
            Assert.Equal("mcd_super-secret-value", store.LoadSecret());
            Assert.True(store.IsRegistered);
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }

    [Fact]
    public void Result_frame_carries_only_known_fields()
    {
        var id = Guid.NewGuid();
        var frame = DeviceAgent.ResultFrame(id, new CommandResult(true, "STARTED", "", 7, "demo-game"));
        Assert.Equal(["type", "command_id", "ok", "code", "message", "detail"], frame.Keys);
    }

    [Fact]
    public void Print_requests_are_held_not_printed()
    {
        var dir = Directory.CreateTempSubdirectory().FullName;
        try
        {
            var doc = Path.Combine(dir, "a.pdf");
            File.WriteAllText(doc, "%PDF");
            var q = new HeldPrintQueue(Path.Combine(dir, "q"));
            q.Submit(doc, 2, false);
            Assert.Equal(2, Assert.Single(q.Held()).Copies);
            Assert.Throws<ArgumentException>(() => q.Submit(Path.Combine(dir, "a.pdf").Replace(".pdf", ".exe").Also(p => File.WriteAllText(p, "")), 1, false));
        }
        finally
        {
            Directory.Delete(dir, true);
        }
    }
}

internal static class Ext
{
    public static string Also(this string s, Action<string> a)
    {
        a(s);
        return s;
    }
}
