# From device readings to an operator dashboard

SitebossHub's original PowerShell system collected infrastructure sensor readings and exposed them through a shared dashboard. This case study describes mechanisms visible in the original source. The runnable Python package is a synthetic reconstruction of the browser/API boundary, not a measurement of the original deployment.

## The operator workflow

The dashboard brings reachability, temperature, humidity, power, propane, door state, and tower-light state into one summary. An operator can move from that summary to a site's sensor readings, short trends, recorded changes, or generator-run information. Collection and presentation are separate: the browser does not need to open SNMP sessions or understand each device's OID map.

### Deployment context

Derek Poe built the first version to support Hurricane Laura recovery. A help desk/admin team used SitebossHub daily to monitor **34 fixed cell tower sites and two mobile tower sites**, sometimes during periods of **24/7 team coverage**.

These deployment details were supplied by the author. They explain who used the system and the operational setting; they are not inferred from demo data or source constants. No measured hours-saved, incident-reduction, or availability figures are claimed. The implemented contribution is a shared summary and drill-down workflow across those sites, backed by automated sensor collection.

## A collection cycle

The reviewed `sitebossPoller.ps1` loads site and sensor mappings from a database, initializes a current-state cache, then walks the sites sequentially:

1. Check reachability. The legacy implementation tries an ICMP probe and, on its fallback path, an HTTP service probe.
2. Open an SNMP session for an eligible site and resolve the configured OID for each sensor.
3. Compare each returned value with the cached value. Write changed values to the current-state database and update the cache.
4. Record reachability/polling state separately so a failed probe does not become a fabricated sensor reading.
5. Complete the site loop and sleep for the unused portion of a nominal 30-second cycle.

The following is a shortened, renamed illustration of the value-change boundary, not a drop-in collector:

```powershell
$value = $snmp.Get($sensorOid)
if ($value -ne $previousValue) {
    # Persist the changed value through the current-state storage boundary.
    # Credentials, database statements, and deployment paths are omitted.
}
$previousValue = $value
```

The cadence logic subtracts time spent doing work:

```powershell
$cycleSeconds = 30
$elapsed = New-TimeSpan -Start $cycleStart -End (Get-Date)
if ($elapsed.TotalSeconds -lt $cycleSeconds) {
    Start-Sleep -Milliseconds (
        $cycleSeconds * 1000 - [Math]::Round($elapsed.TotalMilliseconds)
    )
}
```

Thirty seconds is a source configuration value, not demonstrated throughput. A slow sequential poll can overrun that interval. Timeouts, partial failures, and independently scheduled polling would need explicit treatment in a new production implementation.

## Keeping state useful to the UI

The system separates the current snapshot from historical views. The dashboard relies on stable field names and ordering; short arrays feed Canvas sparklines, while detail routes answer more specific questions. Preserving those contracts allowed a simulator to replace the environment-specific collectors without replacing the operator interface.

The published reconstruction makes that boundary inspectable:

| Behavior | Public implementation or check |
| --- | --- |
| Current site snapshots and bounded history | [`sim.py`](../src/sbh_demo/sim.py) |
| Routes translating state into legacy response shapes | [`app.py`](../src/sbh_demo/app.py) |
| Original browser interaction and Canvas rendering | [`assets/`](../src/sbh_demo/assets/) |
| Concurrent access, deterministic state, bounds, and API contracts | [`tests/`](../tests/) |
| Dashboard drill-down, graphs, downloads, and absence of external requests | [`browser_smoke.py`](../tools/browser_smoke.py) |

The simulator's lock, bounded catch-up, and request validation are maintenance work for this public package. They should not be attributed to the historical PowerShell implementation.

## Evidence boundaries

The original collector also contains environment-specific database access and legacy HTTP/TLS compatibility work. Those operational settings are excluded from this package. Its source was inspected; it was not executed against devices during packaging. The public demo binds to loopback by default and uses synthetic addresses and in-memory data.

The inspected collector snapshot has SHA-256 `610e5776c12966d92f48431ff03a050f385a7b395b2753f8e8f1f926b02a6f37`. This identifies the source used for the implementation account above without publishing its deployment configuration. The author's account supplies the deployment context; source inspection supplies the mechanism descriptions; the synthetic demo and tests establish the published package's behavior.
