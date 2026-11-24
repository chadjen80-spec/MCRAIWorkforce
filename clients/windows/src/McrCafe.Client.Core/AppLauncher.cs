using System.Diagnostics;

namespace McrCafe.Client.Core;

/// <summary>Outcome reported back to the server (and from there to Mira and the devices page).</summary>
public sealed record CommandResult(bool Ok, string Code, string Message = "", int? Pid = null, string? AppId = null);

public interface IProcessStarter
{
    /// <summary>Starts the program and returns its process ID.</summary>
    int Start(ProcessStartInfo info);
}

public sealed class SystemProcessStarter : IProcessStarter
{
    public int Start(ProcessStartInfo info)
    {
        using var p = Process.Start(info) ?? throw new InvalidOperationException("Process did not start");
        return p.Id;
    }
}

/// <summary>Starts an allowlisted program: absolute path, fixed arguments, no shell, no server-supplied text.</summary>
public sealed class AppLauncher(IProcessStarter? starter = null)
{
    private readonly IProcessStarter _starter = starter ?? new SystemProcessStarter();

    public CommandResult Launch(AllowlistEntry entry)
    {
        Allowlist.Validate(entry); // re-check right before use
        if (!File.Exists(entry.Path))
            return new CommandResult(false, "NOT_INSTALLED", $"{entry.Name} is not installed on this PC", AppId: entry.Id);
        var info = new ProcessStartInfo(entry.Path)
        {
            UseShellExecute = false,
            WorkingDirectory = entry.WorkingDir ?? Path.GetDirectoryName(entry.Path)!,
            CreateNoWindow = false,
        };
        foreach (var a in entry.Args ?? [])
            info.ArgumentList.Add(a);
        try
        {
            var pid = _starter.Start(info);
            return new CommandResult(true, "STARTED", "", pid, entry.Id);
        }
        catch (Exception ex) when (ex is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            return new CommandResult(false, "START_FAILED", ex.Message.Length > 200 ? ex.Message[..200] : ex.Message, AppId: entry.Id);
        }
    }
}

/// <summary>Verifier + launcher: turns one signed command into one result. Never throws for a bad command.</summary>
public sealed class CommandProcessor(CommandVerifier verifier, Func<Allowlist> allowlist, AppLauncher launcher)
{
    public (Guid? CommandId, CommandResult Result) Process(string envelope, string signature)
    {
        VerifiedCommand cmd;
        try
        {
            cmd = verifier.Verify(envelope, signature);
        }
        catch (CommandRejectedException ex)
        {
            return (TryCommandId(envelope), new CommandResult(false, ex.Code, ex.Message));
        }
        catch (Exception ex) when (ex is FormatException or ArgumentException)
        {
            return (TryCommandId(envelope), new CommandResult(false, Refusal.BadArgs, "Malformed command"));
        }
        if (cmd.Type == "ping")
            return (cmd.CommandId, new CommandResult(true, "PONG"));
        return allowlist().TryGet(cmd.AppId!, out var entry)
            ? (cmd.CommandId, launcher.Launch(entry))
            : (cmd.CommandId, new CommandResult(false, Refusal.NotAllowlisted, $"'{cmd.AppId}' is not on this PC's allowlist"));
    }

    private static Guid? TryCommandId(string envelope)
    {
        try
        {
            using var doc = System.Text.Json.JsonDocument.Parse(envelope);
            return doc.RootElement.TryGetProperty("command_id", out var v) && Guid.TryParse(v.GetString(), out var g) ? g : null;
        }
        catch (System.Text.Json.JsonException)
        {
            return null;
        }
    }
}
