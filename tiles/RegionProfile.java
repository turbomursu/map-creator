import com.onthegomap.planetiler.Planetiler;
import com.onthegomap.planetiler.FeatureCollector;
import com.onthegomap.planetiler.VectorTile;
import com.onthegomap.planetiler.config.Arguments;
import com.onthegomap.planetiler.geo.GeoUtils;
import com.onthegomap.planetiler.geo.GeometryException;
import com.onthegomap.planetiler.geo.TileCoord;
import com.onthegomap.planetiler.geo.GeometryPipeline;
import com.onthegomap.planetiler.reader.SourceFeature;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.function.Consumer;
import org.locationtech.jts.geom.Geometry;
import org.locationtech.jts.geom.GeometryCollection;
import org.locationtech.jts.geom.Envelope;
import org.locationtech.jts.geom.prep.PreparedGeometry;
import org.locationtech.jts.geom.prep.PreparedGeometryFactory;
import org.locationtech.jts.geom.util.AffineTransformation;
import org.locationtech.jts.io.WKTReader;
import org.locationtech.jts.operation.overlayng.OverlayNG;
import org.locationtech.jts.operation.overlayng.OverlayNGRobust;
import org.openmaptiles.OpenMapTilesProfile;

public class RegionProfile extends OpenMapTilesProfile {
  private final Geometry region;
  private final Geometry[] scaledRegions = new Geometry[15];
  private final PreparedGeometry[] preparedRegions = new PreparedGeometry[15];
  private final PreparedGeometry preparedRegion;

  RegionProfile(Planetiler runner, Geometry region) {
    super(runner);
    this.region = GeoUtils.latLonToWorldCoords(region);
    this.preparedRegion = PreparedGeometryFactory.prepare(this.region);
    for (int z = 0; z < scaledRegions.length; z++) {
      scaledRegions[z] = AffineTransformation.scaleInstance(1L << z, 1L << z).transform(this.region);
      preparedRegions[z] = PreparedGeometryFactory.prepare(scaledRegions[z]);
    }
  }

  private void clipBeforeRendering(FeatureCollector.Feature feature) {
    var previous = new GeometryPipeline[15];
    for (int z = 0; z < previous.length; z++) {
      previous[z] = feature.getScaledGeometryTransformAtZoom(z);
      if (previous[z] == null) previous[z] = GeometryPipeline.defaultSimplify(feature).apply(z);
    }
    // Clip before rendering as well: Planetiler memoizes identical preprocessed tile contents.
    feature.transformScaledGeometryByZoom(zoom -> geometry -> {
      Geometry input = previous[zoom].apply(geometry);
      if (preparedRegions[zoom].covers(input)) return input;
      if (!preparedRegions[zoom].intersects(input)) return input.getFactory().createGeometryCollection();
      Geometry clipped = OverlayNGRobust.overlay(input, scaledRegions[zoom], OverlayNG.INTERSECTION);
      var parts = new ArrayList<Geometry>();
      collect(clipped, input.getDimension(), parts);
      return input.getFactory().buildGeometry(parts);
    });
  }

  @Override
  public void processFeature(SourceFeature source, FeatureCollector features) {
    super.processFeature(source, features);
    for (var feature : features) clipBeforeRendering(feature);
  }

  @Override
  public void finish(String source, FeatureCollector.Factory factory,
      Consumer<FeatureCollector.Feature> next) {
    super.finish(source, factory, feature -> {
      clipBeforeRendering(feature);
      next.accept(feature);
    });
  }

  @Override
  public Map<String, List<VectorTile.Feature>> postProcessTileFeatures(
      TileCoord tile, Map<String, List<VectorTile.Feature>> layers) {
    double scale = 256d * (1L << tile.z());
    Geometry mask = null;
    Map<String, List<VectorTile.Feature>> result = new LinkedHashMap<>();
    for (var entry : layers.entrySet()) {
      try {
        var processed = super.postProcessLayerFeatures(entry.getKey(), tile.z(), entry.getValue());
        var clipped = new ArrayList<VectorTile.Feature>();
        for (var feature : processed == null ? entry.getValue() : processed) {
          if (feature == null) continue;
          Geometry original = feature.geometry().decode();
          var bounds = original.getEnvelopeInternal();
          Geometry worldBounds = original.getFactory().toGeometry(new Envelope(
              (bounds.getMinX() + 256d * tile.x()) / scale,
              (bounds.getMaxX() + 256d * tile.x()) / scale,
              (bounds.getMinY() + 256d * tile.y()) / scale,
              (bounds.getMaxY() + 256d * tile.y()) / scale));
          if (preparedRegion.covers(worldBounds)) {
            clipped.add(feature);
            continue;
          }
          if (mask == null) {
            mask = new AffineTransformation(scale, 0, -256d * tile.x(),
                0, scale, -256d * tile.y()).transform(region);
          }
          Geometry intersection = OverlayNGRobust.overlay(original, mask, OverlayNG.INTERSECTION);
          var parts = new ArrayList<Geometry>();
          collect(intersection, original.getDimension(), parts);
          if (!parts.isEmpty()) {
            clipped.add(feature.copyWithNewGeometry(original.getFactory().buildGeometry(parts)));
          }
        }
        result.put(entry.getKey(), clipped);
      } catch (GeometryException e) {
        // Planetiler's recoverable GeometryException would emit unclipped input.
        throw new IllegalStateException("Cannot safely clip tile " + tile, e);
      }
    }
    return result;
  }

  private static void collect(Geometry geometry, int dimension, List<Geometry> parts) {
    if (geometry.isEmpty()) return;
    if (geometry instanceof GeometryCollection) {
      for (int i = 0; i < geometry.getNumGeometries(); i++) {
        collect(geometry.getGeometryN(i), dimension, parts);
      }
    } else if (geometry.getDimension() == dimension) {
      parts.add(geometry);
    }
  }

  @Override
  public List<VectorTile.Feature> postProcessLayerFeatures(
      String layer, int zoom, List<VectorTile.Feature> features) {
    return features;
  }

  public static void main(String[] args) throws Exception {
    Arguments arguments = Arguments.fromArgs(args);
    Path aoi = arguments.file("aoi", "validated WGS84 polygon WKT", null);
    Geometry region = new WKTReader().read(Files.readString(aoi));
    if (!region.isValid() || region.isEmpty() || region.getDimension() != 2) {
      throw new IllegalArgumentException("AOI must be a nonempty valid polygon");
    }
    var runner = Planetiler.create(arguments);
    runner.setProfile(new RegionProfile(runner, region));
    runner.addOsmSource(OSM_SOURCE, arguments.file("osm_path", "local OSM PBF", null))
      .setOutput(arguments.file("output", "tile archive", null))
      .run();
  }
}
