// Stand-in "game": appends one line (time, PID, arguments) to started.log next to the executable, then exits.
var log = Path.Combine(AppContext.BaseDirectory, "started.log");
File.AppendAllText(log, $"{DateTimeOffset.UtcNow:O} pid={Environment.ProcessId} args={string.Join(' ', args)}{Environment.NewLine}");
Console.WriteLine($"MCR demo game started (pid {Environment.ProcessId}); logged to {log}");
