package com.hpcmap.distance;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.graphhopper.routing.ev.BooleanEncodedValue;
import com.graphhopper.routing.ev.EnumEncodedValue;
import com.graphhopper.routing.ev.RoadClass;
import com.graphhopper.routing.ev.VehicleAccess;
import com.graphhopper.routing.util.AccessFilter;
import com.graphhopper.routing.util.AllEdgesIterator;
import com.graphhopper.routing.util.EncodingManager;
import com.graphhopper.storage.DAType;
import com.graphhopper.storage.Directory;
import com.graphhopper.storage.GHDirectory;
import com.graphhopper.storage.BaseGraph;
import com.graphhopper.storage.StorableProperties;
import com.graphhopper.storage.index.LocationIndex;
import com.graphhopper.storage.index.LocationIndexTree;
import com.graphhopper.storage.index.Snap;
import com.graphhopper.util.EdgeExplorer;
import com.graphhopper.util.EdgeIterator;
import com.graphhopper.util.FetchMode;
import com.graphhopper.util.PointList;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Map;
import java.util.PriorityQueue;
import java.util.Set;

public final class DistanceCoreMain {
    private static final double INF = Double.POSITIVE_INFINITY;
    private static final double CHARGER_CLUSTER_RADIUS_M = 50.0;

    private DistanceCoreMain() {
    }

    public static void main(String[] argv) throws Exception {
        if (argv.length == 1 && ("--help".equals(argv[0]) || "-h".equals(argv[0]))) {
            printUsage();
            return;
        }
        Args args = Args.parse(argv);
        ObjectMapper mapper = new ObjectMapper();

        if (!Files.exists(args.graphCache)) {
            throw new IllegalArgumentException("Graph cache not found: " + args.graphCache);
        }
        if (!Files.exists(args.chargersJson)) {
            throw new IllegalArgumentException("Chargers JSON not found: " + args.chargersJson);
        }

        List<Charger> chargers = readChargers(mapper, args.chargersJson);
        int chargersLoaded = chargers.size();
        chargers = dedupeChargers(chargers, CHARGER_CLUSTER_RADIUS_M);

        LoadedGraph loadedGraph = loadGraphCache(args.graphCache);
        BaseGraph baseGraph = loadedGraph.baseGraph;
        EncodingManager encodingManager = loadedGraph.encodingManager;
        LocationIndex locationIndex = loadedGraph.locationIndex;
        try {
            final BooleanEncodedValue carAccessEnc;
            final BooleanEncodedValue roadClassLinkEnc;
            final EnumEncodedValue<RoadClass> roadClassEnc;
            try {
                carAccessEnc = encodingManager.getBooleanEncodedValue(VehicleAccess.key("car"));
                roadClassLinkEnc = encodingManager.getBooleanEncodedValue("road_class_link");
                roadClassEnc = encodingManager.getEnumEncodedValue(RoadClass.KEY, RoadClass.class);
            } catch (IllegalArgumentException exc) {
                throw new IllegalStateException(
                    "Graph cache is missing required encoded values (car_access/road_class/road_class_link). "
                        + "Re-import GraphHopper cache with updated config/graphhopper.yml.",
                    exc
                );
            }

            List<String> keptChargerIds = null;
            if (args.maxDistanceToMotorwayM != null) {
                List<Charger> filtered = filterChargersByMotorwayDistance(
                    baseGraph,
                    locationIndex,
                    carAccessEnc,
                    roadClassEnc,
                    roadClassLinkEnc,
                    chargers,
                    args.maxDistanceToMotorwayM
                );
                if (filtered.isEmpty()) {
                    throw new IllegalStateException(
                        "No chargers remain after autobahn-direct filter (max_distance_to_motorway_m="
                            + args.maxDistanceToMotorwayM + ")"
                    );
                }
                chargers = filtered;
                keptChargerIds = new ArrayList<>(chargers.size());
                for (Charger charger : chargers) {
                    keptChargerIds.add(charger.id);
                }
            }
            DijkstraResult result = computeDistanceField(baseGraph, locationIndex, carAccessEnc, chargers, args.dropUnsnappable);
            FeatureBuildResult features = buildMotorwaySegments(
                baseGraph,
                roadClassEnc,
                result.distM,
                result.nearestSeedId,
                args.segmentLengthM,
                args.roadClass,
                args.thresholdKw
            );

            ensureParent(args.outSegmentsGeoJson);
            ensureParent(args.outStatsJson);
            mapper.writerWithDefaultPrettyPrinter().writeValue(args.outSegmentsGeoJson.toFile(), features.featureCollection);

            ObjectNode stats = mapper.createObjectNode();
            stats.put("generated_at", Instant.now().toString());
            stats.put("graph_cache", args.graphCache.toString());
            stats.put("objective", args.objective);
            stats.put("road_class", args.roadClass.name());
            stats.put("threshold_kw", args.thresholdKw);
            stats.put("segment_length_m", args.segmentLengthM);
            stats.put("chargers_loaded", chargersLoaded);
            stats.put("chargers_after_50m_dedupe", chargers.size());
            stats.put("seed_nodes", result.seedCount);
            stats.put("unsnappable_chargers", result.unsnappableCount);
            stats.put("reachable_nodes", result.reachableNodeCount);
            stats.put("total_nodes", baseGraph.getNodes());
            stats.put("motorway_edges", features.motorwayEdges);
            stats.put("segments", features.segments);
            if (args.maxDistanceToMotorwayM != null) {
                stats.put("autobahn_direct_filter_max_distance_m", args.maxDistanceToMotorwayM);
                stats.put("chargers_after_autobahn_direct_filter", chargers.size());
                ArrayNode kept = stats.putArray("autobahn_direct_filter_kept_ids");
                if (keptChargerIds != null) {
                    for (String id : keptChargerIds) {
                        kept.add(id);
                    }
                }
            }
            mapper.writerWithDefaultPrettyPrinter().writeValue(args.outStatsJson.toFile(), stats);
        } finally {
            loadedGraph.close();
        }
    }

    private static LoadedGraph loadGraphCache(Path graphCache) {
        Directory directory = new GHDirectory(graphCache.toString(), DAType.MMAP);
        StorableProperties properties = null;
        BaseGraph baseGraph = null;
        LocationIndexTree locationIndex = null;
        try {
            properties = new StorableProperties(directory);
            if (!properties.loadExisting()) {
                throw new IllegalStateException("Failed to load graph properties from " + graphCache);
            }
            EncodingManager encodingManager = EncodingManager.fromProperties(properties);

            baseGraph = new BaseGraph.Builder(encodingManager)
                .setDir(directory)
                .withTurnCosts(encodingManager.needsTurnCostsSupport())
                .build();
            if (!baseGraph.loadExisting()) {
                throw new IllegalStateException("Failed to load base graph from " + graphCache);
            }

            locationIndex = new LocationIndexTree(baseGraph, directory);
            if (!locationIndex.loadExisting()) {
                throw new IllegalStateException("Failed to load location index from " + graphCache);
            }

            return new LoadedGraph(directory, properties, encodingManager, baseGraph, locationIndex);
        } catch (RuntimeException exc) {
            if (locationIndex != null) {
                locationIndex.close();
            }
            if (baseGraph != null) {
                baseGraph.close();
            }
            if (properties != null) {
                properties.close();
            }
            directory.close();
            throw exc;
        }
    }

    private static void printUsage() {
        System.out.println("Usage:");
        System.out.println("  DistanceCoreMain --graph-cache <path> --chargers-json <path> --threshold-kw <n>");
        System.out.println("                   --segment-length-m <n> --road-class MOTORWAY --objective distance");
        System.out.println("                   --drop-unsnappable true|false");
        System.out.println("                   [--max-distance-to-motorway-m <n>]");
        System.out.println("                   --out-segments-geojson <path> --out-stats-json <path>");
    }

    private static void ensureParent(Path path) throws IOException {
        Path parent = path.getParent();
        if (parent != null) {
            Files.createDirectories(parent);
        }
    }

    private static List<Charger> readChargers(ObjectMapper mapper, Path path) throws IOException {
        JsonNode root = mapper.readTree(path.toFile());
        JsonNode arr = root.path("chargers");
        if (!arr.isArray()) {
            return Collections.emptyList();
        }
        List<Charger> out = new ArrayList<>();
        for (JsonNode n : arr) {
            String id = n.path("charger_id").asText("").trim();
            if (id.isEmpty()) {
                continue;
            }
            double lat = n.path("lat").asDouble(Double.NaN);
            double lon = n.path("lon").asDouble(Double.NaN);
            double powerKw = n.path("power_kw").asDouble(Double.NaN);
            if (!Double.isFinite(lat) || !Double.isFinite(lon) || !Double.isFinite(powerKw)) {
                continue;
            }
            String operator = n.path("operator").asText("");
            String status = n.path("status").asText("");
            out.add(new Charger(id, lat, lon, powerKw, operator, status));
        }
        out.sort(Comparator.comparing(c -> c.id));
        return out;
    }

    private static List<Charger> dedupeChargers(List<Charger> chargers, double radiusM) {
        if (chargers.size() <= 1) {
            return chargers;
        }

        List<Charger> ordered = new ArrayList<>(chargers);
        ordered.sort(Comparator.comparing(c -> c.id));

        double refLat = 0.0;
        for (Charger c : ordered) {
            refLat += c.lat;
        }
        refLat /= ordered.size();

        double metersPerDegLon = 111_320.0 * Math.max(Math.cos(Math.toRadians(refLat)), 1e-6);
        double metersPerDegLat = 110_540.0;

        double[] xs = new double[ordered.size()];
        double[] ys = new double[ordered.size()];
        for (int i = 0; i < ordered.size(); i++) {
            xs[i] = ordered.get(i).lon * metersPerDegLon;
            ys[i] = ordered.get(i).lat * metersPerDegLat;
        }

        UnionFind uf = new UnionFind(ordered.size());
        double cellSize = Math.max(radiusM, 1.0);
        Map<Cell, List<Integer>> grid = new HashMap<>();

        for (int i = 0; i < ordered.size(); i++) {
            int cx = (int) Math.floor(xs[i] / cellSize);
            int cy = (int) Math.floor(ys[i] / cellSize);
            for (int nx = cx - 1; nx <= cx + 1; nx++) {
                for (int ny = cy - 1; ny <= cy + 1; ny++) {
                    List<Integer> bucket = grid.get(new Cell(nx, ny));
                    if (bucket == null) {
                        continue;
                    }
                    for (int j : bucket) {
                        if (Math.abs(xs[i] - xs[j]) > radiusM || Math.abs(ys[i] - ys[j]) > radiusM) {
                            continue;
                        }
                        double dM = haversineMeters(ordered.get(i).lat, ordered.get(i).lon, ordered.get(j).lat, ordered.get(j).lon);
                        if (dM <= radiusM) {
                            uf.union(i, j);
                        }
                    }
                }
            }
            grid.computeIfAbsent(new Cell(cx, cy), ignored -> new ArrayList<>()).add(i);
        }

        Map<Integer, List<Integer>> membersByRoot = new HashMap<>();
        for (int i = 0; i < ordered.size(); i++) {
            int root = uf.find(i);
            membersByRoot.computeIfAbsent(root, ignored -> new ArrayList<>()).add(i);
        }

        List<Charger> out = new ArrayList<>();
        for (List<Integer> members : membersByRoot.values()) {
            int best = members.get(0);
            for (int idx : members) {
                Charger cand = ordered.get(idx);
                Charger prev = ordered.get(best);
                if (cand.powerKw > prev.powerKw) {
                    best = idx;
                } else if (cand.powerKw == prev.powerKw && cand.id.compareTo(prev.id) < 0) {
                    best = idx;
                }
            }
            Charger rep = ordered.get(best);
            out.add(new Charger(rep.id, rep.lat, rep.lon, rep.powerKw, rep.operator, rep.status, members.size()));
        }

        out.sort(Comparator.comparing(c -> c.id));
        return out;
    }

    private static DijkstraResult computeDistanceField(
        BaseGraph graph,
        LocationIndex locationIndex,
        BooleanEncodedValue carAccessEnc,
        List<Charger> chargers,
        boolean dropUnsnappable
    ) {
        int nodes = graph.getNodes();
        double[] distM = new double[nodes];
        Arrays.fill(distM, INF);
        String[] nearestSeedId = new String[nodes];

        PriorityQueue<NodeState> pq = new PriorityQueue<>(Comparator
            .comparingDouble((NodeState s) -> s.distM)
            .thenComparing(s -> s.seedId));

        int seedCount = 0;
        int unsnappable = 0;

        for (Charger charger : chargers) {
            Snap snap = locationIndex.findClosest(charger.lat, charger.lon, AccessFilter.allEdges(carAccessEnc));
            if (!snap.isValid()) {
                unsnappable++;
                if (!dropUnsnappable) {
                    throw new IllegalStateException("Unsnappable charger: " + charger.id);
                }
                continue;
            }
            int node = snap.getClosestNode();
            if (node < 0 || node >= nodes) {
                unsnappable++;
                if (!dropUnsnappable) {
                    throw new IllegalStateException("Invalid snap node for charger: " + charger.id);
                }
                continue;
            }
            if (0.0 < distM[node] || (distM[node] == 0.0 && isSeedIdLess(charger.id, nearestSeedId[node]))) {
                if (distM[node] > 0.0) {
                    seedCount++;
                }
                distM[node] = 0.0;
                nearestSeedId[node] = charger.id;
                pq.add(new NodeState(node, 0.0, charger.id));
            }
        }

        EdgeExplorer explorer = graph.createEdgeExplorer();
        while (!pq.isEmpty()) {
            NodeState curr = pq.poll();
            if (curr.distM > distM[curr.node] + 1e-9) {
                continue;
            }
            String currSeed = nearestSeedId[curr.node];
            if (currSeed == null) {
                continue;
            }

            EdgeIterator iter = explorer.setBaseNode(curr.node);
            while (iter.next()) {
                // We need distance *to* chargers for each node. On a directed graph this is
                // a reverse shortest-path problem, so traverse edges with reverse access.
                if (!iter.getReverse(carAccessEnc)) {
                    continue;
                }
                int adj = iter.getAdjNode();
                if (adj < 0 || adj >= nodes) {
                    continue;
                }
                double nextDist = curr.distM + iter.getDistance();
                String prevSeed = nearestSeedId[adj];
                if (nextDist + 1e-9 < distM[adj] || (Math.abs(nextDist - distM[adj]) <= 1e-9 && isSeedIdLess(currSeed, prevSeed))) {
                    distM[adj] = nextDist;
                    nearestSeedId[adj] = currSeed;
                    pq.add(new NodeState(adj, nextDist, currSeed));
                }
            }
        }

        int reachable = 0;
        for (double d : distM) {
            if (Double.isFinite(d)) {
                reachable++;
            }
        }

        return new DijkstraResult(distM, nearestSeedId, seedCount, unsnappable, reachable);
    }

    private static List<Charger> filterChargersByMotorwayDistance(
        BaseGraph graph,
        LocationIndex locationIndex,
        BooleanEncodedValue carAccessEnc,
        EnumEncodedValue<RoadClass> roadClassEnc,
        BooleanEncodedValue roadClassLinkEnc,
        List<Charger> chargers,
        double maxDistanceToMotorwayM
    ) {
        double[] distToMotorwayM = computeDistanceToMotorway(graph, carAccessEnc, roadClassEnc, roadClassLinkEnc);
        List<Charger> out = new ArrayList<>();
        for (Charger charger : chargers) {
            Snap snap = locationIndex.findClosest(charger.lat, charger.lon, AccessFilter.allEdges(carAccessEnc));
            if (!snap.isValid()) {
                continue;
            }
            int node = snap.getClosestNode();
            if (node < 0 || node >= distToMotorwayM.length) {
                continue;
            }
            double d = distToMotorwayM[node];
            if (Double.isFinite(d) && d <= maxDistanceToMotorwayM) {
                out.add(charger);
            }
        }
        out.sort(Comparator.comparing(c -> c.id));
        return out;
    }

    private static double[] computeDistanceToMotorway(
        BaseGraph graph,
        BooleanEncodedValue carAccessEnc,
        EnumEncodedValue<RoadClass> roadClassEnc,
        BooleanEncodedValue roadClassLinkEnc
    ) {
        int nodes = graph.getNodes();
        double[] distM = new double[nodes];
        Arrays.fill(distM, INF);

        Set<Integer> motorwayNodes = new HashSet<>();
        AllEdgesIterator all = graph.getAllEdges();
        while (all.next()) {
            RoadClass rc = all.get(roadClassEnc);
            if (rc != RoadClass.MOTORWAY && !all.get(roadClassLinkEnc)) {
                continue;
            }
            motorwayNodes.add(all.getBaseNode());
            motorwayNodes.add(all.getAdjNode());
        }

        PriorityQueue<NodeState> pq = new PriorityQueue<>(Comparator
            .comparingDouble((NodeState s) -> s.distM)
            .thenComparing(s -> s.seedId));
        for (int node : motorwayNodes) {
            if (node < 0 || node >= nodes) {
                continue;
            }
            distM[node] = 0.0;
            pq.add(new NodeState(node, 0.0, "motorway"));
        }

        EdgeExplorer explorer = graph.createEdgeExplorer();
        while (!pq.isEmpty()) {
            NodeState curr = pq.poll();
            if (curr.distM > distM[curr.node] + 1e-9) {
                continue;
            }
            EdgeIterator iter = explorer.setBaseNode(curr.node);
            while (iter.next()) {
                if (!iter.get(carAccessEnc)) {
                    continue;
                }
                int adj = iter.getAdjNode();
                if (adj < 0 || adj >= nodes) {
                    continue;
                }
                double nextDist = curr.distM + iter.getDistance();
                if (nextDist + 1e-9 < distM[adj]) {
                    distM[adj] = nextDist;
                    pq.add(new NodeState(adj, nextDist, "motorway"));
                }
            }
        }

        return distM;
    }

    private static FeatureBuildResult buildMotorwaySegments(
        BaseGraph graph,
        EnumEncodedValue<RoadClass> roadClassEnc,
        double[] distM,
        String[] nearestSeedId,
        double segmentLengthM,
        RoadClass roadClass,
        double thresholdKw
    ) {
        ObjectMapper mapper = new ObjectMapper();
        ObjectNode featureCollection = mapper.createObjectNode();
        featureCollection.put("type", "FeatureCollection");
        ArrayNode features = featureCollection.putArray("features");

        int motorwayEdges = 0;
        int segments = 0;

        AllEdgesIterator all = graph.getAllEdges();
        while (all.next()) {
            RoadClass rc = all.get(roadClassEnc);
            if (rc != roadClass) {
                continue;
            }
            motorwayEdges++;

            int baseNode = all.getBaseNode();
            int adjNode = all.getAdjNode();
            if (baseNode < 0 || baseNode >= distM.length || adjNode < 0 || adjNode >= distM.length) {
                continue;
            }

            PointList geometry = all.fetchWayGeometry(FetchMode.ALL);
            if (geometry == null || geometry.size() < 2) {
                continue;
            }

            Polyline polyline = Polyline.fromPointList(geometry);
            if (polyline.totalLengthM <= 0.1) {
                continue;
            }

            double dBase = distM[baseNode];
            double dAdj = distM[adjNode];
            String seedBase = nearestSeedId[baseNode];
            String seedAdj = nearestSeedId[adjNode];

            for (double s0 = 0.0; s0 < polyline.totalLengthM - 1e-9; s0 += segmentLengthM) {
                double s1 = Math.min(s0 + segmentLengthM, polyline.totalLengthM);
                Coordinate c0 = polyline.interpolate(s0);
                Coordinate c1 = polyline.interpolate(s1);

                double d0 = minFinite(dBase + s0, dAdj + (polyline.totalLengthM - s0));
                double d1 = minFinite(dBase + s1, dAdj + (polyline.totalLengthM - s1));
                if (!Double.isFinite(d0) || !Double.isFinite(d1)) {
                    continue;
                }

                double mid = (s0 + s1) / 2.0;
                double viaBase = dBase + mid;
                double viaAdj = dAdj + (polyline.totalLengthM - mid);
                String nearestHpc = pickNearestSeed(seedBase, seedAdj, viaBase, viaAdj);

                ObjectNode feature = features.addObject();
                feature.put("type", "Feature");
                ObjectNode props = feature.putObject("properties");
                props.put("edge_id", all.getEdge());
                props.put("min_power_kw", thresholdKw);
                props.put("distance_start_km", d0 / 1000.0);
                props.put("distance_end_km", d1 / 1000.0);
                if (nearestHpc != null) {
                    props.put("nearest_hpc_id", nearestHpc);
                }

                ObjectNode geometryNode = feature.putObject("geometry");
                geometryNode.put("type", "LineString");
                ArrayNode coords = geometryNode.putArray("coordinates");
                ArrayNode p0 = coords.addArray();
                p0.add(c0.lon);
                p0.add(c0.lat);
                ArrayNode p1 = coords.addArray();
                p1.add(c1.lon);
                p1.add(c1.lat);

                segments++;
            }
        }

        return new FeatureBuildResult(featureCollection, motorwayEdges, segments);
    }

    private static boolean isSeedIdLess(String candidate, String existing) {
        if (candidate == null) {
            return false;
        }
        if (existing == null) {
            return true;
        }
        return candidate.compareTo(existing) < 0;
    }

    private static String pickNearestSeed(String seedBase, String seedAdj, double viaBase, double viaAdj) {
        boolean baseFinite = Double.isFinite(viaBase) && seedBase != null;
        boolean adjFinite = Double.isFinite(viaAdj) && seedAdj != null;
        if (baseFinite && !adjFinite) {
            return seedBase;
        }
        if (adjFinite && !baseFinite) {
            return seedAdj;
        }
        if (!baseFinite && !adjFinite) {
            return null;
        }
        if (viaBase < viaAdj) {
            return seedBase;
        }
        if (viaAdj < viaBase) {
            return seedAdj;
        }
        return isSeedIdLess(seedBase, seedAdj) ? seedBase : seedAdj;
    }

    private static double minFinite(double a, double b) {
        boolean fa = Double.isFinite(a);
        boolean fb = Double.isFinite(b);
        if (fa && fb) {
            return Math.min(a, b);
        }
        if (fa) {
            return a;
        }
        if (fb) {
            return b;
        }
        return INF;
    }

    private static double haversineMeters(double lat1, double lon1, double lat2, double lon2) {
        double radius = 6_371_008.8;
        double dLat = Math.toRadians(lat2 - lat1);
        double dLon = Math.toRadians(lon2 - lon1);
        double a = Math.sin(dLat / 2) * Math.sin(dLat / 2)
            + Math.cos(Math.toRadians(lat1)) * Math.cos(Math.toRadians(lat2))
            * Math.sin(dLon / 2) * Math.sin(dLon / 2);
        double c = 2.0 * Math.atan2(Math.sqrt(a), Math.sqrt(1.0 - a));
        return radius * c;
    }

    private record Charger(String id, double lat, double lon, double powerKw, String operator, String status, int siteSize) {
        private Charger(String id, double lat, double lon, double powerKw, String operator, String status) {
            this(id, lat, lon, powerKw, operator, status, 1);
        }
    }

    private record NodeState(int node, double distM, String seedId) {
    }

    private record DijkstraResult(double[] distM, String[] nearestSeedId, int seedCount, int unsnappableCount, int reachableNodeCount) {
    }

    private record FeatureBuildResult(ObjectNode featureCollection, int motorwayEdges, int segments) {
    }

    private static final class LoadedGraph {
        private final Directory directory;
        private final StorableProperties properties;
        private final EncodingManager encodingManager;
        private final BaseGraph baseGraph;
        private final LocationIndex locationIndex;

        private LoadedGraph(
            Directory directory,
            StorableProperties properties,
            EncodingManager encodingManager,
            BaseGraph baseGraph,
            LocationIndex locationIndex
        ) {
            this.directory = directory;
            this.properties = properties;
            this.encodingManager = encodingManager;
            this.baseGraph = baseGraph;
            this.locationIndex = locationIndex;
        }

        private void close() {
            locationIndex.close();
            baseGraph.close();
            properties.close();
            directory.close();
        }
    }

    private record Coordinate(double lon, double lat) {
    }

    private record Cell(int x, int y) {
    }

    private static final class UnionFind {
        private final int[] parent;

        private UnionFind(int n) {
            this.parent = new int[n];
            for (int i = 0; i < n; i++) {
                parent[i] = i;
            }
        }

        private int find(int x) {
            int p = x;
            while (parent[p] != p) {
                parent[p] = parent[parent[p]];
                p = parent[p];
            }
            return p;
        }

        private void union(int a, int b) {
            int ra = find(a);
            int rb = find(b);
            if (ra == rb) {
                return;
            }
            if (ra < rb) {
                parent[rb] = ra;
            } else {
                parent[ra] = rb;
            }
        }
    }

    private static final class Polyline {
        private final double[] lons;
        private final double[] lats;
        private final double[] cumulative;
        private final double totalLengthM;

        private Polyline(double[] lons, double[] lats, double[] cumulative, double totalLengthM) {
            this.lons = lons;
            this.lats = lats;
            this.cumulative = cumulative;
            this.totalLengthM = totalLengthM;
        }

        private static Polyline fromPointList(PointList points) {
            int n = points.size();
            double[] lons = new double[n];
            double[] lats = new double[n];
            double[] cumulative = new double[n];
            double total = 0.0;

            for (int i = 0; i < n; i++) {
                lons[i] = points.getLon(i);
                lats[i] = points.getLat(i);
                if (i > 0) {
                    total += haversineMeters(lats[i - 1], lons[i - 1], lats[i], lons[i]);
                }
                cumulative[i] = total;
            }
            return new Polyline(lons, lats, cumulative, total);
        }

        private Coordinate interpolate(double s) {
            if (s <= 0.0) {
                return new Coordinate(lons[0], lats[0]);
            }
            if (s >= totalLengthM) {
                int last = lons.length - 1;
                return new Coordinate(lons[last], lats[last]);
            }

            int hi = Arrays.binarySearch(cumulative, s);
            if (hi >= 0) {
                return new Coordinate(lons[hi], lats[hi]);
            }
            hi = -hi - 1;
            int lo = hi - 1;
            if (lo < 0) {
                lo = 0;
            }
            if (hi >= cumulative.length) {
                hi = cumulative.length - 1;
            }

            double segStart = cumulative[lo];
            double segEnd = cumulative[hi];
            double segLen = Math.max(segEnd - segStart, 1e-9);
            double t = (s - segStart) / segLen;
            double lon = lons[lo] + (lons[hi] - lons[lo]) * t;
            double lat = lats[lo] + (lats[hi] - lats[lo]) * t;
            return new Coordinate(lon, lat);
        }
    }

    private static final class Args {
        private final Path graphCache;
        private final Path chargersJson;
        private final double thresholdKw;
        private final double segmentLengthM;
        private final String objective;
        private final RoadClass roadClass;
        private final boolean dropUnsnappable;
        private final Double maxDistanceToMotorwayM;
        private final Path outSegmentsGeoJson;
        private final Path outStatsJson;

        private Args(
            Path graphCache,
            Path chargersJson,
            double thresholdKw,
            double segmentLengthM,
            String objective,
            RoadClass roadClass,
            boolean dropUnsnappable,
            Double maxDistanceToMotorwayM,
            Path outSegmentsGeoJson,
            Path outStatsJson
        ) {
            this.graphCache = graphCache;
            this.chargersJson = chargersJson;
            this.thresholdKw = thresholdKw;
            this.segmentLengthM = segmentLengthM;
            this.objective = objective;
            this.roadClass = roadClass;
            this.dropUnsnappable = dropUnsnappable;
            this.maxDistanceToMotorwayM = maxDistanceToMotorwayM;
            this.outSegmentsGeoJson = outSegmentsGeoJson;
            this.outStatsJson = outStatsJson;
        }

        private static Args parse(String[] argv) {
            Map<String, String> map = new HashMap<>();
            for (int i = 0; i < argv.length; i++) {
                String key = argv[i];
                if (!key.startsWith("--")) {
                    throw new IllegalArgumentException("Unexpected argument: " + key);
                }
                if (i + 1 >= argv.length) {
                    throw new IllegalArgumentException("Missing value for: " + key);
                }
                map.put(key, argv[++i]);
            }

            Path graphCache = Path.of(required(map, "--graph-cache"));
            Path chargersJson = Path.of(required(map, "--chargers-json"));
            double thresholdKw = Double.parseDouble(required(map, "--threshold-kw"));
            double segmentLengthM = Double.parseDouble(required(map, "--segment-length-m"));
            String objective = map.getOrDefault("--objective", "distance");
            if (!"distance".equalsIgnoreCase(objective)) {
                throw new IllegalArgumentException("Unsupported objective: " + objective + " (expected: distance)");
            }

            String roadClassRaw = map.getOrDefault("--road-class", "MOTORWAY").trim().toUpperCase();
            RoadClass roadClass = RoadClass.valueOf(roadClassRaw);

            boolean dropUnsnappable = Boolean.parseBoolean(map.getOrDefault("--drop-unsnappable", "true"));
            Double maxDistanceToMotorwayM = map.containsKey("--max-distance-to-motorway-m")
                ? Double.parseDouble(required(map, "--max-distance-to-motorway-m"))
                : null;
            Path outSegmentsGeoJson = Path.of(required(map, "--out-segments-geojson"));
            Path outStatsJson = Path.of(required(map, "--out-stats-json"));

            return new Args(
                graphCache,
                chargersJson,
                thresholdKw,
                segmentLengthM,
                "distance",
                roadClass,
                dropUnsnappable,
                maxDistanceToMotorwayM,
                outSegmentsGeoJson,
                outStatsJson
            );
        }

        private static String required(Map<String, String> map, String key) {
            String value = map.get(key);
            if (value == null || value.isBlank()) {
                throw new IllegalArgumentException("Missing required argument: " + key);
            }
            return value;
        }
    }
}
