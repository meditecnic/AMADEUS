import React, { useState } from 'react';

interface TtsDebugPanelProps {
  sendDebugTts: (emotion: string, text: string) => void;
  isSynthesizing: boolean;
  isPlaying: boolean;
}

type Emotion = 'neutral' | 'tsundere' | 'embarrassed' | 'intellectual' | 'happy' | 'surprised' | 'annoyed' | 'disappointed' | 'sad';

const EMOTIONS: { key: Emotion; label: string; color: string; bg: string; border: string }[] = [
  { key: 'neutral', label: 'Neutral', color: '#b8b8b8', bg: '#1a1a1a', border: '#444' },
  { key: 'tsundere', label: 'Tsundere', color: '#ff9f43', bg: '#1a120a', border: '#8b5a00' },
  { key: 'embarrassed', label: 'Embarrassed', color: '#ff8da1', bg: '#1a0f14', border: '#8b3a5a' },
  { key: 'intellectual', label: 'Intellectual', color: '#a29bfe', bg: '#12101a', border: '#4a3a8b' },
  { key: 'happy', label: 'Happy', color: '#ffeaa7', bg: '#1a1a0f', border: '#8b8b3a' },
  { key: 'surprised', label: 'Surprised', color: '#81ecec', bg: '#0f1a1a', border: '#3a8b8b' },
  { key: 'annoyed', label: 'Annoyed', color: '#ff6b6b', bg: '#1a0f0f', border: '#8b3a3a' },
  { key: 'disappointed', label: 'Disappointed', color: '#b2bec3', bg: '#151718', border: '#5a6368' },
  { key: 'sad', label: 'Sad', color: '#74b9ff', bg: '#0f141a', border: '#3a5a8b' },
];

interface LastResult {
  emotion: string;
  text: string;
  ok: boolean;
}

export const TtsDebugPanel: React.FC<TtsDebugPanelProps> = ({
  sendDebugTts,
  isSynthesizing,
  isPlaying,
}) => {
  const [isOpen, setIsOpen] = useState(false);
  const [text, setText] = useState('て');
  const [lastResult, setLastResult] = useState<LastResult | null>(null);

  const handleEmotionClick = (emotion: string) => {
    if (!text.trim()) return;
    sendDebugTts(emotion, text);
    setLastResult({ emotion, text: text.trim(), ok: true });
  };

  const getStatusLabel = () => {
    if (isSynthesizing) return 'Synthesizing...';
    if (isPlaying) return 'Playing...';
    return 'Idle';
  };

  return (
    <div style={{
      position: 'fixed',
      bottom: 48,
      right: 16,
      zIndex: 9999,
      fontFamily: 'Share Tech Mono, monospace',
      fontSize: 12,
    }}>
      <div
        onClick={() => setIsOpen(!isOpen)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '6px 12px',
          background: '#0c0806',
          border: '1px solid #5a2500',
          cursor: 'pointer',
          color: '#ff6c00',
        }}
      >
        <span>TTS Debug</span>
        <span style={{
          background: '#ff6c00',
          color: '#000',
          padding: '0 4px',
          fontSize: 10,
          fontWeight: 'bold',
        }}>DEV</span>
        <span style={{ color: '#5a2500', fontSize: 10 }}>{isOpen ? '[-]' : '[+]'}</span>
      </div>

      {isOpen && (
        <div style={{
          marginTop: 4,
          padding: 12,
          background: '#0c0806',
          border: '1px solid #5a2500',
          minWidth: 320,
          maxWidth: 380,
        }}>
          <div style={{ marginBottom: 8 }}>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              rows={2}
              style={{
                width: '100%',
                boxSizing: 'border-box',
                resize: 'vertical',
                background: '#1a120a',
                color: '#ff6c00',
                border: '1px solid #5a2500',
                padding: '6px 8px',
                fontFamily: 'Share Tech Mono, monospace',
                fontSize: 13,
                outline: 'none',
              }}
              placeholder="Test text (Japanese recommended)"
            />
          </div>

          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 4,
            marginBottom: 8,
          }}>
            {EMOTIONS.map((e) => (
              <button
                key={e.key}
                onClick={() => handleEmotionClick(e.key)}
                disabled={isSynthesizing || isPlaying || !text.trim()}
                style={{
                  padding: '6px 4px',
                  fontSize: 11,
                  fontFamily: 'Share Tech Mono, monospace',
                  background: e.bg,
                  color: e.color,
                  border: `1px solid ${e.border}`,
                  cursor: (isSynthesizing || isPlaying || !text.trim()) ? 'not-allowed' : 'pointer',
                  opacity: (isSynthesizing || isPlaying) ? 0.5 : 1,
                  transition: 'opacity 0.15s',
                }}
              >
                {e.label}
              </button>
            ))}
          </div>

          <div style={{
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            padding: '6px 8px',
            background: '#0f0a06',
            border: '1px solid #2a1500',
            marginBottom: 8,
            fontSize: 11,
          }}>
            <span style={{ color: '#8b3c00' }}>Status:</span>
            <span style={{
              color: isSynthesizing ? '#ff9f43' : isPlaying ? '#81ecec' : '#5a5a5a',
            }}>
              {getStatusLabel()}
            </span>
          </div>

          {lastResult && (
            <div style={{
              padding: '6px 8px',
              background: '#0f0a06',
              border: '1px solid #2a1500',
              fontSize: 10,
              color: '#8b3c00',
            }}>
              <div style={{ color: '#ff6c00', marginBottom: 2 }}>
                Last: {lastResult.emotion}
              </div>
              <div style={{ color: '#5a5a5a' }}>
                &quot;{lastResult.text}&quot;
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
};

export default TtsDebugPanel;
