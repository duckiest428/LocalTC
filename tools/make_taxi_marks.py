"""Make the taxi guidance's models: a green arrow and a piece of red stop bar, flat on the ground, each a SimObject
(``StaticObject``) the bridge places by its title (``sim_bridge.simconnect_source.MARK_TITLES``).

The files go in ``src/localtc/simpkg/localtc-taxi-guidance`` (the package ``localtc.simpkg`` copies into the sim's
Community folder). The glTF is laid out as the sim's own compiled models are (``ASOBO_asset_optimized``: one
interleaved vertex buffer, 36 bytes a vertex), with no textures: a colour and its glow.

    python tools/make_taxi_marks.py
"""

import json
import struct
import uuid
from pathlib import Path

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "localtc" / "simpkg" / "localtc-taxi-guidance"
LIFT_M = 0.06  # above the ground (the sim's ground is flat at the spot it's made on)

# Each model: its title, folder, colour, and its outline as quads in metres, x right, z ahead (the way it points).
ARROW = [  # a chevron 4 m across, pointing ahead
    [(0.0, 1.9), (2.0, -0.1), (2.0, -1.2), (0.0, 0.8)],
    [(0.0, 1.9), (0.0, 0.8), (-2.0, -1.2), (-2.0, -0.1)],
]
STOP = [[(-2.3, 0.45), (2.3, 0.45), (2.3, -0.45), (-2.3, -0.45)]]  # 4.6 m of a bar across the way
MODELS = (
    ("LocalTC Taxi Arrow", "localtc-taxi-arrow", (0.08, 1.0, 0.22), ARROW),
    ("LocalTC Taxi Stop", "localtc-taxi-stop", (1.0, 0.05, 0.03), STOP),
)


def gltf(name: str, colour: tuple[float, float, float], quads: list) -> tuple[dict, bytes]:
    vertices, indices = [], []
    for quad in quads:
        base = len(vertices)
        vertices += [(x, LIFT_M, z) for x, z in quad]
        indices += [base, base + 1, base + 2, base, base + 2, base + 3]
    data = b""
    for x, y, z in vertices:
        # POSITION 3 floats, TANGENT 4 bytes, NORMAL 4 bytes (up), TEXCOORD_0 and _1 2 shorts each, COLOR_0 4 ushorts
        data += struct.pack("<3f4b4b2h2h4H", x, y, z, 127, 0, 0, 127, 0, 127, 0, 0, 0, 0, 0, 0,
                            65535, 65535, 65535, 65535)
    stride = len(data) // len(vertices)
    index_data = struct.pack(f"<{len(indices)}H", *indices)
    index_data += b"\0" * (-len(index_data) % 4)
    xs, ys, zs = zip(*vertices)
    lo, hi = [min(xs), min(ys), min(zs)], [max(xs), max(ys), max(zs)]
    accessors = [
        {"bufferView": 0, "componentType": 5126, "count": len(vertices), "min": lo, "max": hi, "type": "VEC3",
         "name": f"{name}_POSITION"},
        {"bufferView": 0, "byteOffset": 12, "componentType": 5120, "count": len(vertices), "type": "VEC4",
         "name": f"{name}_TANGENT"},
        {"bufferView": 0, "byteOffset": 16, "componentType": 5120, "count": len(vertices), "type": "VEC4",
         "name": f"{name}_NORMAL"},
        {"bufferView": 0, "byteOffset": 20, "componentType": 5122, "count": len(vertices), "type": "VEC2",
         "name": f"{name}_TEXCOORD_0"},
        {"bufferView": 0, "byteOffset": 24, "componentType": 5122, "count": len(vertices), "type": "VEC2",
         "name": f"{name}_TEXCOORD_1"},
        {"bufferView": 0, "byteOffset": 28, "componentType": 5123, "count": len(vertices), "type": "VEC4",
         "name": f"{name}_COLOR_0"},
        {"bufferView": 1, "componentType": 5123, "count": len(indices), "type": "SCALAR", "name": f"{name}_indices"},
    ]
    doc = {
        "asset": {"generator": "LocalTC tools/make_taxi_marks.py", "version": "2.0", "extensions": {
            "ASOBO_asset_optimized": {"BoundingBoxMax": hi, "BoundingBoxMin": lo, "MajorVersion": 4, "MinorVersion": 3,
                                      "UseCheckerboardMaterialForMissingTextures": True},
            "ASOBO_normal_map_convention": {"tangent_space_convention": "DirectX"}}},
        "extensionsUsed": ["ASOBO_normal_map_convention", "ASOBO_unique_id", "ASOBO_asset_optimized"],
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": name, "extensions": {"ASOBO_unique_id": {"id": name}}}],
        "meshes": [{"name": name, "primitives": [{
            "attributes": {"POSITION": 0, "TANGENT": 1, "NORMAL": 2, "TEXCOORD_0": 3, "TEXCOORD_1": 4, "COLOR_0": 5},
            "indices": 6, "material": 0, "mode": 4,
            "extras": {"ASOBO_primitive": {"PrimitiveCount": len(indices) // 3, "VertexType": "VTX", "VertexVersion": 2}}}]}],
        "materials": [{"name": name, "doubleSided": True, "emissiveFactor": list(colour),
                       "pbrMetallicRoughness": {"baseColorFactor": [*colour, 1.0], "metallicFactor": 0.0,
                                                "roughnessFactor": 0.7}}],
        "accessors": accessors,
        "bufferViews": [
            {"buffer": 0, "byteLength": len(data), "byteStride": stride, "target": 34962, "name": "BufferViewVertexND"},
            {"buffer": 0, "byteOffset": len(data), "byteLength": len(index_data), "target": 34963, "name": "BufferViewIndex"},
        ],
        "buffers": [{"byteLength": len(data) + len(index_data), "uri": f"{name}.bin"}],
    }
    return doc, data + index_data


def main() -> None:
    for title, folder, colour, quads in MODELS:
        base = PACKAGE / "SimObjects" / "Misc" / folder
        model = base / "model"
        model.mkdir(parents=True, exist_ok=True)
        name = folder.replace("-", "_")
        doc, blob = gltf(name, colour, quads)
        (model / f"{name}.gltf").write_text(json.dumps(doc, separators=(",", ":")), encoding="utf-8")
        (model / f"{name}.bin").write_bytes(blob)
        guid = uuid.uuid5(uuid.NAMESPACE_URL, f"localtc:{folder}")
        (model / f"{name}.xml").write_text(
            '<?xml version="1.0" encoding="utf-8"?>\n'
            f'<ModelInfo version="1.1" guid="{{{guid}}}">\n'
            "\t<LODS>\n"
            f'\t\t<LOD minSize="0" ModelFile="{name}.gltf" />\n'
            "\t</LODS>\n"
            "</ModelInfo>\n", encoding="utf-8")
        (model / "model.cfg").write_text(f"[models]\nnormal={name}.xml\n", encoding="utf-8")
        (base / "sim.cfg").write_text(
            "[VERSION]\nMajor=1\nMinor=0\n\n"
            f"[fltsim.0]\ntitle={title}\nmodel=\ntexture=\n\n"
            "[General]\ncategory=StaticObject\n", encoding="utf-8")
    print(f"Made {len(MODELS)} models in {PACKAGE}")


if __name__ == "__main__":
    main()
