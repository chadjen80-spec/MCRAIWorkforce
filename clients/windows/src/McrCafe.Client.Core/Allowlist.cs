using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

namespace McrCafe.Client.Core;

/// <summary>One approved program on this PC. Written by the café administrator, never by the server.</summary>
public sealed record AllowlistEntry(
    [property: JsonPropertyName("id")] string Id,
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("path")] string Path,
    [property: JsonPropertyName("args")] IReadOnlyList<string>? Args = null,
    [property: JsonPropertyName("working_dir")] string? WorkingDir = null);

/// <summary>
/// The only programs this client can start. The server sends an ID; the path and arguments come from here, so a
/// compromised server still cannot run anything the shop did not approve on this PC.
/// </summary>
public sealed partial class Allowlist
{
    // Interpreters and script hosts are refused even if an administrator lists them: they would turn "launch an app" into
    // "run any command".
    private static readonly HashSet<string> BlockedExecutables = new(StringComparer.OrdinalIgnoreCase)
    {
        "cmd.exe", "powershell.exe", "pwsh.exe", "powershell_ise.exe", "wscript.exe", "cscript.exe", "mshta.exe",
        "rundll32.exe", "regsvr32.exe", "msiexec.exe", "bash.exe", "wsl.exe", "conhost.exe", "explorer.exe",
        "reg.exe", "regedit.exe", "schtasks.exe", "sc.exe", "net.exe", "certutil.exe", "bitsadmin.exe", "wmic.exe",
    };

    [GeneratedRegex("^[a-z0-9][a-z0-9_-]{0,63}$")]
    internal static partial Regex IdPattern();

    private readonly Dictionary<string, AllowlistEntry> _entries;

    public Allowlist(IEnumerable<AllowlistEntry> entries)
    {
        _entries = new Dictionary<string, AllowlistEntry>(StringComparer.Ordinal);
        foreach (var e in entries)
        {
            Validate(e);
            if (!_entries.TryAdd(e.Id, e))
                throw new AllowlistException($"Duplicate id '{e.Id}'");
        }
    }

    public IReadOnlyCollection<AllowlistEntry> Entries => _entries.Values;

    public bool TryGet(string id, out AllowlistEntry entry) => _entries.TryGetValue(id, out entry!);

    /// <summary>"installed" when the executable exists on disk, otherwise "missing". Reported in every heartbeat.</summary>
    public Dictionary<string, string> InstallStatus() =>
        _entries.Values.ToDictionary(e => e.Id, e => File.Exists(e.Path) ? "installed" : "missing");

    public static void Validate(AllowlistEntry e)
    {
        if (string.IsNullOrEmpty(e.Id) || !IdPattern().IsMatch(e.Id))
            throw new AllowlistException($"Invalid id '{e.Id}' (lowercase letters, digits, '-' and '_')");
        if (string.IsNullOrWhiteSpace(e.Path) || !System.IO.Path.IsPathFullyQualified(e.Path))
            throw new AllowlistException($"'{e.Id}': path must be absolute");
        if (e.Path.StartsWith(@"\\", StringComparison.Ordinal))
            throw new AllowlistException($"'{e.Id}': network paths are not allowed");
        if (!string.Equals(System.IO.Path.GetExtension(e.Path), ".exe", StringComparison.OrdinalIgnoreCase))
            throw new AllowlistException($"'{e.Id}': only .exe programs can be allowlisted");
        if (BlockedExecutables.Contains(System.IO.Path.GetFileName(e.Path)))
            throw new AllowlistException($"'{e.Id}': {System.IO.Path.GetFileName(e.Path)} is a shell or system tool and cannot be allowlisted");
    }

    public static Allowlist Load(string file)
    {
        if (!File.Exists(file))
            return new Allowlist([]);
        var doc = JsonSerializer.Deserialize<AllowlistFile>(File.ReadAllText(file))
                  ?? throw new AllowlistException("Allowlist file is empty");
        return new Allowlist(doc.Apps ?? []);
    }

    private sealed record AllowlistFile([property: JsonPropertyName("apps")] List<AllowlistEntry>? Apps);
}

public sealed class AllowlistException(string message) : Exception(message);
