using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace McrCafe.Client.Core;

/// <summary>Where the client keeps its files. Default: %ProgramData%\MCR\SmartCafe (ACL it to Administrators + SYSTEM).</summary>
public sealed class ClientPaths(string dataDir)
{
    public static string DefaultDataDir =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "MCR", "SmartCafe");

    public string DataDir { get; } = Directory.CreateDirectory(dataDir).FullName;
    public string Device => Path.Combine(DataDir, "device.json");
    public string Secret => Path.Combine(DataDir, "device.secret");
    public string ServerKey => Path.Combine(DataDir, "server_public_key.pem");
    public string Allowlist => Path.Combine(DataDir, "allowlist.json");
    public string Replay => Path.Combine(DataDir, "replay.json");
    public string PrintQueue => Path.Combine(DataDir, "print-queue");
}

/// <summary>Non-secret identity of this PC (device.json).</summary>
public sealed record DeviceIdentity(
    [property: JsonPropertyName("server")] string Server,
    [property: JsonPropertyName("device_id")] Guid DeviceId,
    [property: JsonPropertyName("organization_id")] Guid OrganizationId,
    [property: JsonPropertyName("seat_label")] string SeatLabel);

/// <summary>
/// Stores the device identity, the pinned server key and the device secret. The secret is encrypted with Windows DPAPI
/// (machine scope, plus entropy) so copying the file to another PC does not reveal it.
/// </summary>
public sealed class DeviceStore(ClientPaths paths)
{
    private static readonly byte[] Entropy = Encoding.UTF8.GetBytes("MCR.SmartCafe.DeviceSecret.v1");

    public ClientPaths Paths { get; } = paths;

    public bool IsRegistered => File.Exists(Paths.Device) && File.Exists(Paths.Secret) && File.Exists(Paths.ServerKey);

    public void Save(DeviceIdentity identity, string secret, string serverPublicKeyPem)
    {
        File.WriteAllText(Paths.Device, JsonSerializer.Serialize(identity, new JsonSerializerOptions { WriteIndented = true }));
        File.WriteAllBytes(Paths.Secret, ProtectedData.Protect(Encoding.UTF8.GetBytes(secret), Entropy, DataProtectionScope.LocalMachine));
        File.WriteAllText(Paths.ServerKey, serverPublicKeyPem);
    }

    public DeviceIdentity LoadIdentity() =>
        JsonSerializer.Deserialize<DeviceIdentity>(File.ReadAllText(Paths.Device)) ?? throw new InvalidOperationException("device.json is empty");

    public string LoadSecret() =>
        Encoding.UTF8.GetString(ProtectedData.Unprotect(File.ReadAllBytes(Paths.Secret), Entropy, DataProtectionScope.LocalMachine));

    public string LoadServerKey() => File.ReadAllText(Paths.ServerKey);
}
