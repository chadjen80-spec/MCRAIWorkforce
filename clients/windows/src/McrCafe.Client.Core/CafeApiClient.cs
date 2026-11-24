using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace McrCafe.Client.Core;

public sealed record RegisterResponse(
    [property: JsonPropertyName("device_id")] Guid DeviceId,
    [property: JsonPropertyName("device_secret")] string DeviceSecret,
    [property: JsonPropertyName("organization_id")] Guid OrganizationId,
    [property: JsonPropertyName("seat_label")] string SeatLabel,
    [property: JsonPropertyName("server_public_key")] string ServerPublicKey);

public sealed record SeatStatus(
    [property: JsonPropertyName("seat_label")] string SeatLabel,
    [property: JsonPropertyName("session_active")] bool SessionActive,
    [property: JsonPropertyName("remaining_seconds")] int RemainingSeconds);

public sealed class CafeApiException(int status, string code, string message) : Exception(message)
{
    public int Status { get; } = status;
    public string Code { get; } = code;
}

/// <summary>HTTP calls to /v1/cafe/device. Refuses plain HTTP except to this machine (development).</summary>
public sealed class CafeApiClient : IDisposable
{
    private readonly HttpClient _http;
    private string? _token;
    private DateTimeOffset _tokenExpires;

    public CafeApiClient(string server, HttpMessageHandler? handler = null)
    {
        Server = RequireSecure(server);
        _http = handler is null ? new HttpClient() : new HttpClient(handler);
        _http.BaseAddress = Server;
        _http.Timeout = TimeSpan.FromSeconds(20);
    }

    public Uri Server { get; }

    public static Uri RequireSecure(string server)
    {
        var uri = new Uri(server.TrimEnd('/') + "/");
        if (uri.Scheme == Uri.UriSchemeHttps || (uri.Scheme == Uri.UriSchemeHttp && uri.IsLoopback))
            return uri;
        throw new ArgumentException("The server must use https:// (plain http is allowed only for localhost)");
    }

    public Uri WebSocketUri
    {
        get
        {
            var u = new Uri(Server, "v1/cafe/device/ws");
            return new UriBuilder(u) { Scheme = u.Scheme == Uri.UriSchemeHttps ? "wss" : "ws" }.Uri;
        }
    }

    public async Task<RegisterResponse> RegisterAsync(string code, string name, string version, CancellationToken ct = default) =>
        await SendAsync<RegisterResponse>(HttpMethod.Post, "v1/cafe/device/register",
            new { enrollment_code = code, name, client_version = version, os_version = Environment.OSVersion.VersionString }, false, ct);

    public async Task<string> TokenAsync(Guid deviceId, string secret, CancellationToken ct = default)
    {
        if (_token is not null && DateTimeOffset.UtcNow < _tokenExpires)
            return _token;
        var res = await SendAsync<JsonElement>(HttpMethod.Post, "v1/cafe/device/token", new { device_id = deviceId, device_secret = secret }, false, ct);
        _token = res.GetProperty("access_token").GetString();
        _tokenExpires = DateTimeOffset.UtcNow.AddSeconds(res.GetProperty("expires_in").GetInt32() - 60);
        return _token!;
    }

    public void ForgetToken() => _token = null;

    public Task<SeatStatus> HeartbeatAsync(string version, Dictionary<string, string> installed, CancellationToken ct = default) =>
        SendAsync<SeatStatus>(HttpMethod.Post, "v1/cafe/device/heartbeat",
            new { client_version = version, os_version = Environment.OSVersion.VersionString, installed_apps = installed }, true, ct);

    public Task<SeatStatus> StatusAsync(CancellationToken ct = default) => SendAsync<SeatStatus>(HttpMethod.Get, "v1/cafe/device/status", null, true, ct);

    public Task<JsonElement> CommandsAsync(CancellationToken ct = default) => SendAsync<JsonElement>(HttpMethod.Get, "v1/cafe/device/commands", null, true, ct);

    public Task<JsonElement> ResultAsync(Guid commandId, CommandResult r, CancellationToken ct = default) =>
        SendAsync<JsonElement>(HttpMethod.Post, $"v1/cafe/device/commands/{commandId}/result", ResultBody(r), true, ct);

    public Task<JsonElement> AssistAsync(string message, CancellationToken ct = default) =>
        SendAsync<JsonElement>(HttpMethod.Post, "v1/cafe/device/assist", new { message }, true, ct);

    public static object ResultBody(CommandResult r) => new
    {
        ok = r.Ok,
        code = r.Code,
        message = r.Message,
        detail = r.Pid is null ? new Dictionary<string, object>() : new Dictionary<string, object> { ["pid"] = r.Pid, ["app_id"] = r.AppId ?? "" },
    };

    private async Task<T> SendAsync<T>(HttpMethod method, string path, object? body, bool auth, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(method, path);
        if (body is not null)
            req.Content = JsonContent.Create(body);
        if (auth)
            req.Headers.Authorization = new("Bearer", _token ?? throw new InvalidOperationException("Not authenticated"));
        using var resp = await _http.SendAsync(req, ct);
        var text = await resp.Content.ReadAsStringAsync(ct);
        if (!resp.IsSuccessStatusCode)
        {
            string code = "HTTP_ERROR", message = text;
            try
            {
                var err = JsonDocument.Parse(text).RootElement.GetProperty("error");
                code = err.GetProperty("code").GetString() ?? code;
                message = err.GetProperty("message").GetString() ?? message;
            }
            catch (Exception e) when (e is JsonException or KeyNotFoundException or InvalidOperationException)
            {
            }
            if ((int)resp.StatusCode == 401)
                _token = null;
            throw new CafeApiException((int)resp.StatusCode, code, message);
        }
        return JsonSerializer.Deserialize<T>(text)!;
    }

    public void Dispose() => _http.Dispose();
}
