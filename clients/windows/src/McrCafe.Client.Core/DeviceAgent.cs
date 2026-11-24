using System.Net.WebSockets;
using System.Text;
using System.Text.Json;

namespace McrCafe.Client.Core;

/// <summary>
/// The running client: keeps a WebSocket to the server (secure in production), sends a heartbeat with install status
/// every few seconds, verifies and executes signed commands and reports their results. Reconnects with backoff and asks
/// for a fresh token when the server closes the socket because the token expired.
/// </summary>
public sealed class DeviceAgent(DeviceStore store, CafeApiClient api, string clientVersion, Action<string>? log = null)
{
    public TimeSpan HeartbeatInterval { get; init; } = TimeSpan.FromSeconds(10);

    /// <summary>Latest seat status from the server; the display counts the remaining time down locally between updates.</summary>
    public SeatStatus? Status { get; private set; }

    public DateTimeOffset StatusAt { get; private set; }
    public bool Connected { get; private set; }

    private readonly Action<string> _log = log ?? (_ => { });

    public CommandProcessor BuildProcessor()
    {
        var id = store.LoadIdentity();
        var replay = new ReplayCache(store.Paths.Replay);
        Allowlist Current() => Allowlist.Load(store.Paths.Allowlist); // re-read so an administrator's edit applies at once
        var verifier = new CommandVerifier(store.LoadServerKey(), id.DeviceId, id.OrganizationId, replay, Current);
        return new CommandProcessor(verifier, Current, new AppLauncher());
    }

    public int RemainingSecondsNow() =>
        Status is null ? 0 : Math.Max(0, Status.RemainingSeconds - (int)(DateTimeOffset.UtcNow - StatusAt).TotalSeconds);

    public async Task RunAsync(CancellationToken ct)
    {
        var processor = BuildProcessor();
        var id = store.LoadIdentity();
        var delay = TimeSpan.FromSeconds(1);
        while (!ct.IsCancellationRequested)
        {
            try
            {
                var token = await api.TokenAsync(id.DeviceId, store.LoadSecret(), ct);
                await RunSocketAsync(token, processor, ct);
                delay = TimeSpan.FromSeconds(1);
            }
            catch (OperationCanceledException) when (ct.IsCancellationRequested)
            {
                break;
            }
            catch (Exception ex) when (ex is WebSocketException or HttpRequestException or CafeApiException or IOException)
            {
                _log($"connection problem: {ex.Message}");
                if (ex is CafeApiException { Status: 401 })
                    api.ForgetToken();
            }
            Connected = false;
            await Task.Delay(delay, ct).ContinueWith(_ => { }, CancellationToken.None);
            delay = TimeSpan.FromSeconds(Math.Min(30, delay.TotalSeconds * 2));
        }
    }

    private async Task RunSocketAsync(string token, CommandProcessor processor, CancellationToken ct)
    {
        using var ws = new ClientWebSocket();
        ws.Options.KeepAliveInterval = TimeSpan.FromSeconds(20);
        await ws.ConnectAsync(api.WebSocketUri, ct);
        await SendAsync(ws, new { type = "auth", token }, ct);
        var sendLock = new SemaphoreSlim(1, 1);
        using var linked = CancellationTokenSource.CreateLinkedTokenSource(ct);
        var heartbeat = Task.Run(async () =>
        {
            while (!linked.Token.IsCancellationRequested && ws.State == WebSocketState.Open)
            {
                var installed = Allowlist.Load(store.Paths.Allowlist).InstallStatus();
                await Locked(sendLock, () => SendAsync(ws, new { type = "heartbeat", client_version = clientVersion, installed_apps = installed }, linked.Token));
                await Task.Delay(HeartbeatInterval, linked.Token);
            }
        }, linked.Token);
        try
        {
            while (ws.State == WebSocketState.Open && !ct.IsCancellationRequested)
            {
                var msg = await ReceiveAsync(ws, ct);
                if (msg is null)
                    break;
                var root = msg.Value;
                switch (root.GetProperty("type").GetString())
                {
                    case "welcome":
                        Connected = true;
                        _log("connected");
                        break;
                    case "status":
                        Status = root.Deserialize<SeatStatus>();
                        StatusAt = DateTimeOffset.UtcNow;
                        break;
                    case "command":
                        var (commandId, result) = processor.Process(root.GetProperty("envelope").GetString() ?? "", root.GetProperty("signature").GetString() ?? "");
                        _log($"command {commandId}: {result.Code} {result.Message}".TrimEnd());
                        if (commandId is not null)
                        {
                            var body = ResultFrame(commandId.Value, result);
                            await Locked(sendLock, () => SendAsync(ws, body, ct));
                        }
                        break;
                    case "error":
                        _log($"server: {root.GetProperty("code").GetString()}");
                        break;
                }
            }
            if (ws.CloseStatus is { } status && (int)status == 4401)
                api.ForgetToken();
            _log($"socket closed: {ws.CloseStatusDescription}");
        }
        finally
        {
            Connected = false;
            await linked.CancelAsync();
            try
            {
                await heartbeat;
            }
            catch (Exception)
            {
                // the heartbeat loop ends with the socket
            }
        }
    }

    internal static Dictionary<string, object?> ResultFrame(Guid commandId, CommandResult r) => new()
    {
        ["type"] = "result",
        ["command_id"] = commandId,
        ["ok"] = r.Ok,
        ["code"] = r.Code,
        ["message"] = r.Message,
        ["detail"] = r.Pid is null ? new Dictionary<string, object>() : new Dictionary<string, object> { ["pid"] = r.Pid, ["app_id"] = r.AppId ?? "" },
    };

    private static async Task Locked(SemaphoreSlim gate, Func<Task> action)
    {
        await gate.WaitAsync();
        try
        {
            await action();
        }
        finally
        {
            gate.Release();
        }
    }

    private static Task SendAsync(ClientWebSocket ws, object body, CancellationToken ct) =>
        ws.SendAsync(Encoding.UTF8.GetBytes(JsonSerializer.Serialize(body)), WebSocketMessageType.Text, true, ct);

    private static async Task<JsonElement?> ReceiveAsync(ClientWebSocket ws, CancellationToken ct)
    {
        var buffer = new byte[16 * 1024];
        using var ms = new MemoryStream();
        while (true)
        {
            var r = await ws.ReceiveAsync(buffer, ct);
            if (r.MessageType == WebSocketMessageType.Close)
                return null;
            ms.Write(buffer, 0, r.Count);
            if (ms.Length > 256 * 1024)
                throw new WebSocketException("Message too large");
            if (r.EndOfMessage)
                break;
        }
        return JsonDocument.Parse(ms.ToArray()).RootElement.Clone();
    }
}
