# HPC Distance Map Pipeline (Germany Motorways → Nearest ≥150kW Charger)

## Goal

Compute and visualize:

> The shortest driving distance from every location on German motorways  
> to the nearest HPC charging station (≥150 kW)

Final output:

- Vector tiles (`.mbtiles`)
- Smooth, zoomable motorway distance map (like your screenshot)
- Hoverable segments with distance info

---

# Architecture Overview

We split the system into **three reusable layers**:

## 1. Routing Layer (GraphHopper) → reusable artifact
- Builds the routable graph from OSM
- Provides snapping + routing
- Used by all downstream steps

## 2. Compute Layer (distance field)
- Runs multi-source shortest path from all HPCs
- Produces a distance field over the graph

## 3. Visualization Layer (tiles)
- Extracts motorway edges
- Converts to colored segments
- Exports MBTiles

---

# 1. GraphHopper Setup (Reusable Artifact)

## Purpose

Create a **stable, reusable routing graph + server** that is used by all later steps.

This is your **core artifact**:
- built once
- reused many times

---

## 1.1 Config: `graphhopper-hpc.yml`

```yaml
graphhopper:
  datareader.file: /data/germany-latest.osm.pbf
  graph.location: /data/graph-cache-germany-car

  graph.encoded_values: road_class,road_class_link,road_environment,road_access,max_speed,surface

  graph.flag_encoders: car|turn_costs=true

  profiles:
    - name: car
      vehicle: car
      weighting: fastest
      turn_costs: true
      u_turn_costs: 60

  profiles_ch:
    - profile: car

server:
  application_connectors:
    - type: http
      port: 8989
  admin_connectors:
    - type: http
      port: 8990

logging:
  level: INFO
```

---

## 1.2 Build Graph (one-time)

```bash
java -jar graphhopper-web-<version>.jar server graphhopper-hpc.yml
```

On first run:
- imports OSM
- builds routing graph
- stores at `/data/graph-cache-germany-car`

---

## 1.3 Reuse Graph

On subsequent runs:
- **no re-import happens**
- graph is reused from disk
- server starts instantly

---

## 1.4 Artifact Summary

You now have:

- Graph cache (disk)
- Local routing server (`localhost:8989`)

This is your **foundation**.

---

# 2. Distance Field Computation

## Core Idea

Instead of:
> motorway point → all chargers

We do:
> all chargers → entire graph (once)

This is a **multi-source shortest path problem**.

---

## 2.1 Inputs

### HPC CSV
```
id,lat,lon,power_kw
```

Filter:
```
power_kw >= 150
```
This is take from the bnetza CSV in this project. Make sure to deduplicate chargers that are almost at the same position (within 50m) after filtering the power
---

## 2.2 Snap HPCs to Graph

For each charger:
- snap to GraphHopper graph
- get:
  - snapped edge
  - snapped coordinate

---

## 2.3 Multi-Source Dijkstra

Initialize:

```
for each snapped HPC:
    distance = 0
    push into priority queue
```

Run Dijkstra over **full car graph**.

---

## Output

For every node:

```
distance_to_nearest_hpc[node]
```

Optional:
```
nearest_hpc_id[node]
```

---

## Important

Computation runs on:

- **full road graph** (not motorway-only)

Because routes include:
- motorway
- exits
- local roads

---

# 3. Extract Motorway Distance Field

## 3.1 Filter Motorway Edges

Iterate all edges:

Keep only:

```
road_class == MOTORWAY
```

---

## 3.2 Edge Distance Model

For edge:

```
A ----(length L)---- B
```

We know:

```
dA = distance(A)
dB = distance(B)
```

---

## Distance at position s on edge:

```
dist(s) = min(
    dA + s,
    dB + (L - s)
)
```

---

## 3.3 Create Render Segments

Split each motorway edge into small segments:

- recommended: 250 m (maybe?!)

For each segment:
- compute midpoint
- assign distance
--> check  if this is meaningful...
---

## Output Feature

Each segment:

```
LineString
distance_m
distance_km
edge_id
(optional) nearest_hpc_id
```

---

# 4. Export Geospatial Data

## Intermediate Format

Use one of:

- GeoJSON (simple)
- FlatGeobuf (fast + compact)
- GeoPackage

---

## Example Feature

```json
{
  "type": "Feature",
  "geometry": { "type": "LineString", "coordinates": [...] },
  "properties": {
    "distance_km": 7.4,
    "edge_id": 123456
  }
}
```

---

# 5. Convert to MBTiles (Vector Tiles)

## Why MBTiles

- smooth zooming
- small size
- fast rendering
- supports styling
-> (how is smooth coloring done?)
---

## Tool: tippecanoe

```bash
tippecanoe \
  -o motorway_distance.mbtiles \
  -l motorway_distance \
  -zg \
  --drop-densest-as-needed \
  motorway.geojson
```

---

## Optional Layer: HPC stations
Can be activated with a checkbox and should be a geojson format because otherwise grouping looks bad in the map.
Add second layer:

```
hpc_stations
```

---

# 6. Frontend Rendering

Use:

- MapLibre GL JS

---

## Line Styling

Color by:

```
distance_km
```

Example:

- 0–5 → green
- 5–10 → yellow-green
- 10–15 → yellow
- 15–20 → orange
- 20+ → red

---

## Interaction

On hover:
- show distance
- show nearest HPC (optional)

---

# 7. Pipeline Summary

## Build Phase (once)
1. configure GraphHopper
2. import OSM
3. create graph cache

---

## Compute Phase
4. load graph
5. snap HPCs
6. run multi-source Dijkstra
7. compute node distances

---

## Extraction Phase
8. filter motorway edges
9. split into segments
10. assign distance values

---

## Tile Phase
11. export GeoJSON
12. convert to MBTiles

---

## Render Phase
13. load tiles in map
14. style + interact

---

# Key Insights

## 1. Do NOT sample points for routing
Sampling is only for rendering.

## 2. Compute once, reuse everywhere
Distance field = reusable dataset

## 3. Full graph is required for correctness
Motorway-only graph is insufficient

## 4. Lines > points
Always render line segments, not point clouds

## 5. MBTiles is a good choice
Efficient + scalable + frontend-friendly

---

# Suggested Defaults

| Parameter            | Value        |
|---------------------|-------------|
| Segment length      | 250 m       |
| Profile             | car         |
| Weighting           | fastest     |
| Charger threshold   | ≥150 kW     |

---

# Future Improvements

- store nearest HPC id per segment
- compute travel time instead of distance
- filter by charger operator/network
- incremental updates
- motorway-only reduced graph optimization

---

# Final Outcome

You end up with:

- one reusable GraphHopper graph/server
- one computed distance field
- one MBTiles file

→ plug directly into your map UI  
→ smooth zoom  
→ consistent coloring  
→ no sampling artifacts

---