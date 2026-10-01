# Third-party notices

The root MIT license applies to this project's original source code. Third-party software, models and fonts retain their own licenses. STEINS;GATE characters, names and original materials are not licensed by this project's MIT license.

## Bundled fonts

The font files under `desktop/public/assets/fonts/s4-ds/` use the SIL Open Font License 1.1. Their copyright notices and license text are preserved in [OFL.txt](desktop/public/assets/fonts/s4-ds/OFL.txt). This includes Oxanium, IBM Plex Mono and Noto Sans CJK families.

## Dependencies

Python, npm and Cargo dependencies are installed separately; their source, wheels and binaries are not vendored in this repository. Keep the upstream notices and satisfy the selected versions' licenses when distributing dependencies or a packaged application.

The dependency inventory includes MPL 2.0 components (`certifi`, `tqdm`, `cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext`, `selectors`). These licenses apply to the respective components, not to this project's original source files. The `r-efi` license offers MIT or Apache 2.0 alternatives.

Speech recognition uses [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) (Apache 2.0). SenseVoice weights are subject to their own model license and are not distributed in this repository. Optional local weights are downloaded on first use; their upstream license is preserved with them.

The connection page uses [Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT). Vendor names and marks belong to their respective owners.

Upstream references: [MPL FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/), [OFL FAQ](https://openfontlicense.org/ofl-faq/), [SenseVoice models](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html).
