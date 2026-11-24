using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace McrCafe.Client.Core;

/// <summary>A command that passed every check and may be executed.</summary>
public sealed record VerifiedCommand(Guid CommandId, string Type, string? AppId);

/// <summary>Why a command was refused. Codes match the server's result codes.</summary>
public static class Refusal
{
    public const string SignatureInvalid = "SIGNATURE_INVALID";
    public const string WrongDevice = "WRONG_DEVICE";
    public const string Expired = "EXPIRED";
    public const string Replayed = "REPLAYED";
    public const string UnknownType = "UNKNOWN_TYPE";
    public const string BadArgs = "BAD_ARGS";
    public const string NotAllowlisted = "NOT_ALLOWLISTED";
}

public sealed class CommandRejectedException(string code, string message) : Exception(message)
{
    public string Code { get; } = code;
}

/// <summary>
/// Verifies a signed command from the server, in this order: ECDSA P-256 signature over the exact envelope text, envelope
/// version, this device and organisation, expiry (with clock-skew allowance), replay, known type, argument shape, and that
/// the app ID is on the local allowlist. Nothing is executed here.
/// </summary>
public sealed class CommandVerifier
{
    public static readonly TimeSpan ClockSkew = TimeSpan.FromSeconds(30);
    private static readonly string[] KnownTypes = ["launch_app", "ping"];

    private readonly ECDsa _serverKey;
    private readonly Guid _deviceId;
    private readonly Guid _organizationId;
    private readonly ReplayCache _replay;
    private readonly Func<Allowlist> _allowlist;
    private readonly TimeProvider _time;

    public CommandVerifier(string serverPublicKeyPem, Guid deviceId, Guid organizationId, ReplayCache replay, Func<Allowlist> allowlist,
                           TimeProvider? time = null)
    {
        _serverKey = ECDsa.Create();
        _serverKey.ImportFromPem(serverPublicKeyPem);
        if (_serverKey.KeySize != 256)
            throw new ArgumentException("Server key must be ECDSA P-256");
        _deviceId = deviceId;
        _organizationId = organizationId;
        _replay = replay;
        _allowlist = allowlist;
        _time = time ?? TimeProvider.System;
    }

    public VerifiedCommand Verify(string envelope, string signature)
    {
        byte[] sig;
        try
        {
            sig = Base64UrlDecode(signature);
        }
        catch (FormatException)
        {
            throw new CommandRejectedException(Refusal.SignatureInvalid, "Signature is not base64url");
        }
        if (sig.Length != 64 || !_serverKey.VerifyData(Encoding.UTF8.GetBytes(envelope), sig, HashAlgorithmName.SHA256,
                                                         DSASignatureFormat.IeeeP1363FixedFieldConcatenation))
            throw new CommandRejectedException(Refusal.SignatureInvalid, "Signature does not match the pinned server key");

        JsonElement root;
        try
        {
            root = JsonDocument.Parse(envelope).RootElement;
        }
        catch (JsonException)
        {
            throw new CommandRejectedException(Refusal.BadArgs, "Envelope is not JSON");
        }
        if (Int(root, "v") != 1)
            throw new CommandRejectedException(Refusal.BadArgs, "Unsupported envelope version");
        var commandId = Guid.Parse(Str(root, "command_id"));
        if (Guid.Parse(Str(root, "device_id")) != _deviceId || Guid.Parse(Str(root, "org_id")) != _organizationId)
            throw new CommandRejectedException(Refusal.WrongDevice, "Command is addressed to another device");

        var now = _time.GetUtcNow();
        var issued = DateTimeOffset.FromUnixTimeSeconds(Int(root, "issued_at"));
        var expires = DateTimeOffset.FromUnixTimeSeconds(Int(root, "expires_at"));
        if (now > expires + ClockSkew || issued > now + ClockSkew || expires - issued > TimeSpan.FromMinutes(10))
            throw new CommandRejectedException(Refusal.Expired, "Command is expired or not yet valid");

        if (!_replay.TryAdd(commandId, expires + ClockSkew))
            throw new CommandRejectedException(Refusal.Replayed, "Command was already received");

        var type = Str(root, "type");
        if (Array.IndexOf(KnownTypes, type) < 0)
            throw new CommandRejectedException(Refusal.UnknownType, $"Unknown command type '{type}'");
        if (type == "ping")
            return new VerifiedCommand(commandId, type, null);

        if (!root.TryGetProperty("args", out var args) || args.ValueKind != JsonValueKind.Object ||
            !args.TryGetProperty("app_id", out var appIdEl) || appIdEl.ValueKind != JsonValueKind.String)
            throw new CommandRejectedException(Refusal.BadArgs, "launch_app needs args.app_id");
        var appId = appIdEl.GetString()!;
        if (!Allowlist.IdPattern().IsMatch(appId) || args.EnumerateObject().Count() != 1)
            throw new CommandRejectedException(Refusal.BadArgs, "launch_app accepts only an app_id");
        if (!_allowlist().TryGet(appId, out _))
            throw new CommandRejectedException(Refusal.NotAllowlisted, $"'{appId}' is not on this PC's allowlist");
        return new VerifiedCommand(commandId, type, appId);
    }

    private static string Str(JsonElement root, string name) =>
        root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String
            ? v.GetString()!
            : throw new CommandRejectedException(Refusal.BadArgs, $"Missing {name}");

    private static long Int(JsonElement root, string name) =>
        root.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.Number && v.TryGetInt64(out var n)
            ? n
            : throw new CommandRejectedException(Refusal.BadArgs, $"Missing {name}");

    internal static byte[] Base64UrlDecode(string s)
    {
        var t = s.Replace('-', '+').Replace('_', '/');
        return Convert.FromBase64String(t.PadRight(t.Length + (4 - t.Length % 4) % 4, '='));
    }
}
