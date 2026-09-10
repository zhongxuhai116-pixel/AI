from pathlib import Path
import sys

import bpy


def add_box(name, location, scale, color):
    bpy.ops.mesh.primitive_cube_add(location=location)
    obj = bpy.context.object
    obj.name = name
    obj.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    material = bpy.data.materials.new(f"{name} Material")
    material.diffuse_color = (*color, 1)
    obj.data.materials.append(material)


def main():
    output = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.object.delete(use_global=False)
    add_box("Product Body", (0, 0, .65), (.72, .42, .48), (.08, .32, .72))
    add_box("Product Top", (0, 0, 1.17), (.46, .31, .08), (.8, .86, .95))
    add_box("Product Detail", (0, -.44, .67), (.22, .035, .14), (.9, .36, .12))
    bpy.ops.export_scene.gltf(filepath=str(output), export_format="GLB")


if __name__ == "__main__":
    main()
