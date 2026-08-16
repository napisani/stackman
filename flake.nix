{
  description = "Stacked pull request manager for local Git workflows";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = nixpkgs.legacyPackages.${system};
        inherit (pkgs) lib;
        python = pkgs.python312;
        pythonEnv = python.withPackages (ps: [
          ps.click
          ps.pytest
        ]);

        stackman = python.pkgs.buildPythonApplication {
          pname = "stackman";
          version = "0.1.0";
          pyproject = true;
          src = lib.cleanSource ./.;

          build-system = [ python.pkgs.hatchling ];
          dependencies = [ python.pkgs.click ];

          nativeCheckInputs = [
            pkgs.git
            python.pkgs.pytestCheckHook
          ];
          pytestFlags = [ "tests" ];
          pythonImportsCheck = [ "stackman" ];
          preCheck = ''
            export HOME="$TMPDIR/home"
            mkdir -p "$HOME"
          '';

          meta = {
            description = "Stacked pull request manager for local Git workflows";
            mainProgram = "stackman";
            platforms = lib.platforms.unix;
          };
        };
      in
      {
        formatter = pkgs.nixfmt;

        packages.default = stackman;

        apps.default = flake-utils.lib.mkApp { drv = stackman; };

        devShells.default = pkgs.mkShell {
          packages = [
            pythonEnv
            pkgs.git
            pkgs.gnumake
            pkgs.ruff
            pkgs.ty
            pkgs.uv
          ];
        };

        checks = {
          package = stackman;

          quality =
            pkgs.runCommand "stackman-quality"
              {
                nativeBuildInputs = [
                  pythonEnv
                  pkgs.ruff
                  pkgs.ty
                ];
              }
              ''
                cp -R ${lib.cleanSource ./.} source
                chmod -R u+w source
                cd source
                ruff format --check src tests
                ruff check src tests
                ty check src/stackman
                touch "$out"
              '';

          smoke = pkgs.runCommand "stackman-smoke" { } ''
            ${lib.getExe stackman} --help > "$out"
          '';
        };
      }
    );
}
