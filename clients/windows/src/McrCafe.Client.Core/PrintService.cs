using System.Text.Json;

namespace McrCafe.Client.Core;

public sealed record PrintRequest(Guid Id, string FileName, int Copies, bool Color, DateTimeOffset At);

/// <summary>Print service integration point. The café printing module (pricing, staff release) is not built yet.</summary>
public interface IPrintService
{
    PrintRequest Submit(string file, int copies, bool color);
    IReadOnlyList<PrintRequest> Held();
}

/// <summary>
/// v0.1 stub: copies the document into a local "held" folder with a manifest so staff can release it by hand. Nothing is
/// sent to a printer and nothing is charged. Replaced by the printing module (server-side job, per-page price, release).
/// </summary>
public sealed class HeldPrintQueue(string folder) : IPrintService
{
    private static readonly string[] Allowed = [".pdf", ".docx", ".xlsx", ".pptx", ".jpg", ".jpeg", ".png", ".txt"];

    public PrintRequest Submit(string file, int copies, bool color)
    {
        if (!File.Exists(file))
            throw new FileNotFoundException("File not found", file);
        if (Array.IndexOf(Allowed, Path.GetExtension(file).ToLowerInvariant()) < 0)
            throw new ArgumentException("This file type cannot be printed here");
        if (copies is < 1 or > 50)
            throw new ArgumentOutOfRangeException(nameof(copies));
        Directory.CreateDirectory(folder);
        var req = new PrintRequest(Guid.NewGuid(), Path.GetFileName(file), copies, color, DateTimeOffset.UtcNow);
        File.Copy(file, Path.Combine(folder, $"{req.Id}{Path.GetExtension(file)}"));
        File.WriteAllText(Path.Combine(folder, $"{req.Id}.json"), JsonSerializer.Serialize(req));
        return req;
    }

    public IReadOnlyList<PrintRequest> Held() =>
        Directory.Exists(folder)
            ? Directory.GetFiles(folder, "*.json").Select(f => JsonSerializer.Deserialize<PrintRequest>(File.ReadAllText(f))!).OrderBy(r => r.At).ToList()
            : [];
}
