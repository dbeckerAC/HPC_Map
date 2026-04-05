import maplibregl from "maplibre-gl";
import "./styles.css";

const API_BASE = "http://localhost:8000";
const TILESERVER_BASE = "http://localhost:8080";
const DISTANCE_TILES = `${TILESERVER_BASE}/data/hpc_distance/{z}/{x}/{y}.pbf`;
const HPC_TILES = `${TILESERVER_BASE}/data/hpc_sites/{z}/{x}/{y}.pbf`;
const hoverEl = document.getElementById("hover");
const hpcToggle = document.getElementById("hpc-toggle");

const map = new maplibregl.Map({
  container: "map",
  center: [10.5, 51.1],
  zoom: 5.6,
  style: {
    version: 8,
    sources: {
      osm: {
        type: "raster",
        tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"],
        tileSize: 256,
        attribution: "&copy; OpenStreetMap contributors"
      }
    },
    layers: [
      {
        id: "osm",
        type: "raster",
        source: "osm",
        paint: {
          "raster-opacity": 0.62,
          "raster-saturation": -0.75,
          "raster-contrast": 0.15,
          "raster-brightness-max": 0.78
        }
      }
    ]
  }
});

async function getLayerMode() {
  try {
    const response = await fetch(`${API_BASE}/layers/status`);
    if (!response.ok) {
      return "geojson";
    }
    const status = await response.json();
    if (status.distance_mbtiles_exists) {
      return "vector";
    }
    if (status.distance_geojson_exists) {
      return "geojson";
    }
  } catch {
    return "geojson";
  }
  return "geojson";
}

function addDistanceLayer(source, sourceLayer = null) {
  const casing = {
    id: "hpc-distance-casing",
    type: "line",
    source,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 2.6, 12, 6.5],
      "line-color": "#0f141a",
      "line-opacity": 0.55
    }
  };
  if (sourceLayer) {
    casing["source-layer"] = sourceLayer;
  }
  map.addLayer(casing);

  const layer = {
    id: "hpc-distance",
    type: "line",
    source,
    layout: {
      "line-cap": "round",
      "line-join": "round"
    },
    paint: {
      "line-width": ["interpolate", ["linear"], ["zoom"], 5, 1.8, 12, 4.6],
      "line-color": [
        "interpolate",
        ["linear"],
        ["coalesce", ["get", "distance_start_km"], 20],
        0,
        "#16a34a",
        8,
        "#facc15",
        14,
        "#f59e0b",
        20,
        "#dc2626"
      ],
      "line-opacity": 0.96
    }
  };
  if (sourceLayer) {
    layer["source-layer"] = sourceLayer;
  }
  map.addLayer(layer);
}

function addHpcLayer(source, sourceLayer = null) {
  const clusters = {
    id: "hpc-clusters",
    type: "circle",
    source,
    filter: ["has", "point_count"],
    paint: {
      "circle-color": "#0b57d0",
      "circle-opacity": 0.82,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 1.0,
      "circle-radius": [
        "interpolate",
        ["linear"],
        ["get", "point_count"],
        10,
        12,
        50,
        17,
        200,
        22,
        1000,
        28
      ]
    }
  };
  const clusterCount = {
    id: "hpc-cluster-count",
    type: "symbol",
    source,
    filter: ["has", "point_count"],
    layout: {
      "text-field": ["to-string", ["get", "point_count_abbreviated"]],
      "text-size": 11
    },
    paint: {
      "text-color": "#ffffff"
    }
  };
  const points = {
    id: "hpc-sites",
    type: "circle",
    source,
    filter: ["!", ["has", "point_count"]],
    minzoom: 9,
    paint: {
      "circle-radius": ["interpolate", ["linear"], ["zoom"], 9, 2.0, 12, 4.5],
      "circle-color": "#1155cc",
      "circle-opacity": 0.88,
      "circle-stroke-color": "#ffffff",
      "circle-stroke-width": 0.9
    }
  };
  if (sourceLayer) {
    clusters["source-layer"] = sourceLayer;
    clusterCount["source-layer"] = sourceLayer;
    points["source-layer"] = sourceLayer;
  }
  map.addLayer(clusters);
  map.addLayer(clusterCount);
  map.addLayer(points);
}

map.on("load", async () => {
  const mode = await getLayerMode();
  if (mode === "vector") {
    map.addSource("distance", { type: "vector", tiles: [DISTANCE_TILES], minzoom: 4, maxzoom: 14 });
    map.addSource("hpc", {
      type: "geojson",
      data: `${API_BASE}/layers/hpc-sites.geojson`,
      cluster: true,
      clusterRadius: 42,
      clusterMaxZoom: 8
    });
    addDistanceLayer("distance", "hpc_distance");
    addHpcLayer("hpc");
    hoverEl.textContent = "Hover a motorway segment";
    bindHoverEvents();
    return;
  }
  map.addSource("distance", { type: "geojson", data: `${API_BASE}/layers/hpc-distance.geojson` });
  map.addSource("hpc", {
    type: "geojson",
    data: `${API_BASE}/layers/hpc-sites.geojson`,
    cluster: true,
    clusterRadius: 42,
    clusterMaxZoom: 8
  });
  addDistanceLayer("distance");
  addHpcLayer("hpc");
  hoverEl.textContent = "Hover a motorway segment (GeoJSON mode)";
  bindHoverEvents();
});

function bindHoverEvents() {
  map.on("mousemove", "hpc-distance", (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const start = Number(feature.properties?.distance_start_km ?? NaN);
    const end = Number(feature.properties?.distance_end_km ?? NaN);
    const minPower = feature.properties?.min_power_kw ?? "n/a";
    const avg = Number.isFinite(start) && Number.isFinite(end) ? ((start + end) / 2).toFixed(2) : "n/a";
    hoverEl.textContent = `Distance: ${avg} km | threshold: ${minPower} kW`;
  });

  map.on("mouseleave", "hpc-distance", () => {
    hoverEl.textContent = "Hover a motorway segment";
  });

  map.on("mouseenter", "hpc-sites", () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mouseenter", "hpc-clusters", () => {
    map.getCanvas().style.cursor = "pointer";
  });

  map.on("mousemove", "hpc-sites", (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const chargerId = feature.properties?.charger_id ?? "n/a";
    const power = feature.properties?.power_kw ?? "n/a";
    const status = feature.properties?.status || "n/a";
    hoverEl.textContent = `HPC ${chargerId} | ${power} kW | ${status}`;
  });

  map.on("mouseleave", "hpc-sites", () => {
    map.getCanvas().style.cursor = "";
    hoverEl.textContent = "Hover a motorway segment";
  });

  map.on("mousemove", "hpc-clusters", (event) => {
    const feature = event.features?.[0];
    if (!feature) return;
    const count = feature.properties?.point_count_abbreviated ?? feature.properties?.point_count ?? "n/a";
    hoverEl.textContent = `HPC cluster: ${count} stations`;
  });

  map.on("mouseleave", "hpc-clusters", () => {
    map.getCanvas().style.cursor = "";
    hoverEl.textContent = "Hover a motorway segment";
  });

  if (hpcToggle) {
    hpcToggle.addEventListener("change", () => {
      const visibility = hpcToggle.checked ? "visible" : "none";
      for (const layerId of ["hpc-clusters", "hpc-cluster-count", "hpc-sites"]) {
        if (map.getLayer(layerId)) {
          map.setLayoutProperty(layerId, "visibility", visibility);
        }
      }
    });
  }
}
