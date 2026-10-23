# Third-party notices

The root MIT license applies to this project's original source code. Third-party software, models and fonts retain their own licenses. STEINS;GATE characters, names and original materials are not licensed by this project's MIT license.

## Worldline transition runtime

- `desktop/src/worldline-engine/` vendors the procedural WebGL2 runtime from [meditecnic/worldline-transition](https://github.com/meditecnic/worldline-transition), copyright (c) 2026 meditecnic, under MIT. Its original [LICENSE](desktop/src/worldline-engine/LICENSE) and [source/trim notes](desktop/src/worldline-engine/README.vendor.md) are retained. Demo font binaries and recorded media are excluded; the upstream font notices remain in `assets/OFL.txt`.

## Fonts

- `desktop/public/assets/fonts/s4-ds/`: Oxanium, IBM Plex Mono and Noto Sans CJK families use SIL Open Font License 1.1. Copyright and license text are preserved in [OFL.txt](desktop/public/assets/fonts/s4-ds/OFL.txt).
- The root font file `ShareTechMono-Regular.woff2` also uses SIL Open Font License 1.1. Upstream copyright and license reference: [Share Tech Mono](https://github.com/google/fonts/blob/main/ofl/sharetechmono/OFL.txt).
- `desktop/public/assets/fonts/memory/LXGWNeoXiHei.ttf` uses IPA Font License 1.0. Its [license](desktop/public/assets/fonts/memory/LICENSE.md) and [provenance](desktop/public/assets/fonts/memory/PROVENANCE.md) are preserved.
- Fontsource EB Garamond, Noto Serif JP and Noto Serif SC retain the OFL notices supplied in their npm packages.

## Speech and memory models

- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) source uses Apache 2.0. Official 1.13.8 wheels also include the [espeak-ng component](https://github.com/csukuangfj/espeak-ng/blob/ed530aa113046142eb5115cf2fc9157854d0ffe1/COPYING), licensed under GPLv3. The repository does not vendor or distribute these wheels. A packaged application must assess the selected wheel and satisfy its component licenses; the ASR-only build or distribution choice remains a packaging decision.
- [SenseVoice](https://github.com/FunAudioLLM/SenseVoice) source uses MIT; official weights have the separate FunASR Model Open Source License v1.1. The [converted sherpa-onnx package](https://k2-fsa.github.io/sherpa/onnx/sense-voice/pretrained.html) is downloaded separately, and its license reference is retained with the model. Model weights are not bundled in the repository.
- Silero VAD uses the [upstream MIT license](https://github.com/snakers4/silero-vad/blob/master/LICENSE); its ONNX file is downloaded separately.
- [multilingual-e5-small](https://huggingface.co/intfloat/multilingual-e5-small) uses MIT and is downloaded separately when enabled.
- [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) uses MIT. It runs as a separate local server; the repository does not vendor or distribute it.
- The optional voice model [TTS-KurisuMakise](https://huggingface.co/bysq/TTS-KurisuMakise) is published by bysq and labeled Apache-2.0 by its uploader. This project does not include or download its weights or any reference audio. The model imitates the voice of the STEINS;GATE character Makise Kurisu; rights in the character, the original recordings and the voice actor's performance belong to their respective owners and are not licensed by this project.

## Installed dependencies

Python, npm and Cargo dependencies are installed separately. Preserve the licenses and notices from the selected distributions when packaging them.

SciPy uses BSD-3-Clause. Its wheels may also bundle OpenBLAS (BSD-3-Clause), libgfortran (GPL-3.0-or-later with GCC Runtime Library Exception 3.1) and libquadmath (LGPL-2.1-or-later), depending on the target platform. Keep the wheel's complete license inventory and notices; the [GCC runtime exception](https://www.gnu.org/licenses/gcc-exception-3.1.html) applies to the relevant runtime component.

MPL 2.0 components include `certifi`, `tqdm`, `cssparser`, `cssparser-macros`, `dtoa-short`, `option-ext` and `selectors`. These licenses apply to the respective components. `r-efi` offers MIT or Apache 2.0 alternatives.

The connection page uses [Lobe Icons](https://github.com/lobehub/lobe-icons) (MIT). Vendor names and marks belong to their owners.
