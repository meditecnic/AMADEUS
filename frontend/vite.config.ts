import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import fs from 'fs';

export default defineConfig({
  plugins: [
    react(),
    {
      name: 'cleanup-unused-assets',
      apply: 'build',
      closeBundle() {
        const distDir = path.resolve(__dirname, 'dist');
        const audioDir = path.join(distDir, 'assets/audio');
        if (fs.existsSync(audioDir)) {
          // 保留运行时实际引用的音频（BGM + 看门狗 SFX），删除其余未使用文件以兼顾包体积。
          // 引用源：App.tsx 的 playBGM('/assets/audio/ringtone_gate_of_steiner.ogg') 与
          // playSFX('/assets/audio/gah.ogg')；iOS Safari 下 useAudio.ts 会把 .ogg 改写为 .mp3。
          const allowedAudio = new Set<string>([
            'ringtone_gate_of_steiner.ogg',
            'ringtone_gate_of_steiner.mp3',
            'gah.ogg',
            'gah.mp3'
          ]);
          for (const file of fs.readdirSync(audioDir)) {
            if (!allowedAudio.has(file)) {
              try {
                fs.unlinkSync(path.join(audioDir, file));
              } catch (e) {}
            }
          }
        }

        const imagesDir = path.join(distDir, 'assets/images');
        if (fs.existsSync(imagesDir)) {
          const files = fs.readdirSync(imagesDir);
          
          const allowedImages = new Set<string>();
          allowedImages.add('ic_launcher.png');
          allowedImages.add('icon.ico');
          
          for (let i = 1; i <= 38; i++) {
            allowedImages.add(`logo${i}.png`);
          }
          
          const expressions = [
            'kurisu_normal1.png', 'kurisu_normal2.png', 'kurisu_normal3.png', 'kurisu_eyes_closed1.png',
            'kurisu_sided_angry1.png', 'kurisu_sided_angry2.png', 'kurisu_sided_angry3.png', 'kurisu_sided_eyes_closed1.png',
            'kurisu_sided_blush1.png', 'kurisu_sided_blush2.png', 'kurisu_sided_blush3.png',
            'kurisu_sided_thinking1.png', 'kurisu_sided_thinking2.png', 'kurisu_sided_thinking3.png'
          ];
          expressions.forEach(img => allowedImages.add(img));
          
          files.forEach(file => {
            if (!allowedImages.has(file)) {
              try {
                fs.unlinkSync(path.join(imagesDir, file));
              } catch (e) {}
            }
          });
        }
      }
    }
  ],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/ws': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        changeOrigin: true
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
});
