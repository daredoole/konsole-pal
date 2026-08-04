{
  description = "Konsole Pal — local-first attention routing for terminal AI agents";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
    in {
      packages = eachSystem (system:
        let pkgs = import nixpkgs { inherit system; };
        in {
          default = pkgs.python3Packages.buildPythonApplication {
            pname = "konsole-pal";
            version = "1.0.1";
            src = self;
            pyproject = true;
            build-system = [ pkgs.python3Packages.setuptools ];
            nativeCheckInputs = [ pkgs.python3Packages.tomli ];
            pythonImportsCheck = [ "konsole_pal" ];
            meta = {
              description = "Tiny local-first attention router for terminal AI agents";
              homepage = "https://github.com/daredoole/konsole-pal";
              license = pkgs.lib.licenses.mit;
              mainProgram = "konsole-pal";
              platforms = pkgs.lib.platforms.unix;
            };
          };
        });
      checks = eachSystem (system: {
        package = self.packages.${system}.default;
      });
    };
}
