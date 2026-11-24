using System.Text.Json;

namespace McrCafe.Client.Core;

/// <summary>Command IDs already received, kept until shortly after they expire. Persisted so a restart cannot replay one.</summary>
public sealed class ReplayCache
{
    private readonly Dictionary<Guid, DateTimeOffset> _seen = [];
    private readonly string? _file;
    private readonly TimeProvider _time;
    private readonly Lock _lock = new();

    public ReplayCache(string? file = null, TimeProvider? time = null)
    {
        _file = file;
        _time = time ?? TimeProvider.System;
        if (_file is not null && File.Exists(_file))
        {
            try
            {
                foreach (var (k, v) in JsonSerializer.Deserialize<Dictionary<Guid, DateTimeOffset>>(File.ReadAllText(_file)) ?? [])
                    _seen[k] = v;
            }
            catch (JsonException)
            {
                // A damaged cache is replaced; expiry checks still refuse old commands.
            }
        }
    }

    /// <summary>False when the ID was seen before (a replay).</summary>
    public bool TryAdd(Guid id, DateTimeOffset keepUntil)
    {
        lock (_lock)
        {
            var now = _time.GetUtcNow();
            foreach (var old in _seen.Where(p => p.Value < now).Select(p => p.Key).ToList())
                _seen.Remove(old);
            if (!_seen.TryAdd(id, keepUntil))
                return false;
            if (_file is not null)
                File.WriteAllText(_file, JsonSerializer.Serialize(_seen));
            return true;
        }
    }
}
