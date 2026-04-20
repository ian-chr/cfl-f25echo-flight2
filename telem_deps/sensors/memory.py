def read_memory_used_free_kb():
    total = available = None
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    total = int(line.split()[1])
                elif line.startswith("MemAvailable:"):
                    available = int(line.split()[1])
                if total is not None and available is not None:
                    break
    except Exception:
        return (None, None)
    if total is None or available is None:
        return (None, None)
    return (total - available, available)