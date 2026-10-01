import os
import subprocess

ffmpeg_path = "ffmpeg"  # 请修改为自己的 ffmpeg 路径
audio_dir = os.path.join(os.path.dirname(__file__), "public", "assets", "audio")

converted_count = 0
failed_count = 0

for file in os.listdir(audio_dir):
    if file.endswith(".ogg"):
        input_path = os.path.join(audio_dir, file)
        output_path = os.path.join(audio_dir, file.replace(".ogg", ".mp3"))
        if not os.path.exists(output_path):
            print(f"Converting {file} to MP3...")
            res = subprocess.run([
                ffmpeg_path,
                "-y",
                "-i", input_path,
                "-codec:a", "libmp3lame",
                "-qscale:a", "2",
                output_path
            ], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if res.returncode != 0:
                print(f"Failed to convert {file}: {res.stderr.decode(errors='ignore')}")
                failed_count += 1
            else:
                converted_count += 1

print(f"Finished: {converted_count} files converted successfully, {failed_count} failures.")
