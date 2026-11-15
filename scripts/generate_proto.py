# Generates the Python gRPC code from the .proto files in /proto. The generated code is committed.
# Run from the repo root on the host after changing a .proto file:
#   pip install grpcio-tools==1.85.1 protobuf==7.35.1
#   python scripts/generate_proto.py
# grpcio-tools 1.85.1 bundles protoc 35.1, so its code needs protobuf >= 7.35.1 and grpcio >= 1.85.1 at runtime
# (the versions pinned in the services' requirements.txt). Change all three pins together.
import pathlib

from grpc_tools import protoc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO_DIR = REPO_ROOT / "proto"

# Each output folder and the .proto files it gets: every service only its own, the scripts folder all three
TARGETS = {
    "services/order/app/generated": ["order.proto"],
    "services/knowledge/app/generated": ["knowledge.proto"],
    "services/ticket/app/generated": ["ticket.proto"],
    "scripts/generated": ["order.proto", "knowledge.proto", "ticket.proto"],
}

for output_folder, proto_files in TARGETS.items():
    output_dir = REPO_ROOT / output_folder

    # Step 1: create the folder as a Python package
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "__init__.py").write_text("# Generated gRPC code. Do not edit; run scripts/generate_proto.py instead.\n")

    # Step 2: run protoc for the message code (*_pb2.py) and the gRPC stubs (*_pb2_grpc.py)
    exit_code = protoc.main([
        "grpc_tools.protoc",
        f"--proto_path={PROTO_DIR}",
        f"--python_out={output_dir}",
        f"--grpc_python_out={output_dir}",
        *[str(PROTO_DIR / proto_file) for proto_file in proto_files],
    ])
    if exit_code != 0:
        raise SystemExit(f"protoc failed for {output_folder} (exit code {exit_code})")

    # Step 3: protoc writes "import order_pb2 as order__pb2", which only works from the same folder.
    # Rewrite it to a relative import so the code works inside a package (app.generated, generated).
    for proto_file in proto_files:
        module_name = proto_file.removesuffix(".proto") + "_pb2"
        grpc_file = output_dir / f"{module_name}_grpc.py"
        code = grpc_file.read_text()
        code = code.replace(f"\nimport {module_name} as ", f"\nfrom . import {module_name} as ")
        grpc_file.write_text(code)

    print(f"Generated {', '.join(proto_files)} into {output_folder}")
