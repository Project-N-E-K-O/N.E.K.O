import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  Alert,
} from 'react-native';
import { router } from 'expo-router';
import { WebView } from 'react-native-webview';

export default function ViewerScreen() {
  const [isLive2DVisible, setIsLive2DVisible] = useState(true);
  const [currentEmotion, setCurrentEmotion] = useState('normal');
  const webViewRef = useRef<WebView>(null);

  const emotions = [
    { id: 'normal', name: '正常' },
    { id: 'happy', name: '开心' },
    { id: 'sad', name: '悲伤' },
    { id: 'angry', name: '生气' },
    { id: 'surprised', name: '惊讶' },
    { id: 'wink', name: '眨眼' },
    { id: 'laugh', name: '大笑' },
    { id: 'cry', name: '哭泣' },
  ];

  const toggleLive2D = () => {
    setIsLive2DVisible(!isLive2DVisible);
  };

  const changeEmotion = (emotion: string) => {
    setCurrentEmotion(emotion);
    // 这里可以发送消息到WebView来改变表情
    if (webViewRef.current) {
      webViewRef.current.postMessage(JSON.stringify({
        type: 'changeEmotion',
        emotion: emotion
      }));
    }
  };

  const handleWebViewMessage = (event: any) => {
    try {
      const data = JSON.parse(event.nativeEvent.data);
      console.log('WebView message:', data);
    } catch (error) {
      console.error('Error parsing WebView message:', error);
    }
  };

  const live2dHTML = `
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Live2D Viewer</title>
        <style>
            body {
                margin: 0;
                padding: 0;
                background: transparent;
                overflow: hidden;
            }
            #live2d-container {
                width: 100%;
                height: 100vh;
                display: flex;
                justify-content: center;
                align-items: center;
            }
            #live2d-canvas {
                max-width: 100%;
                max-height: 100%;
            }
        </style>
    </head>
    <body>
        <div id="live2d-container">
            <canvas id="live2d-canvas" width="800" height="600"></canvas>
        </div>
        <script>
            // 这里可以加载Live2D相关的JavaScript
            // 由于移动端限制，这里只是占位符
            console.log('Live2D Viewer loaded');
            
            // 监听来自React Native的消息
            window.addEventListener('message', function(event) {
                const data = JSON.parse(event.data);
                if (data.type === 'changeEmotion') {
                    console.log('Changing emotion to:', data.emotion);
                    // 这里实现表情切换逻辑
                }
            });
        </script>
    </body>
    </html>
  `;

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶部控制栏 */}
      <View style={styles.header}>
        <TouchableOpacity 
          style={styles.backButton}
          onPress={() => router.back()}
        >
          <Text style={styles.backButtonText}>返回</Text>
        </TouchableOpacity>
        <Text style={styles.title}>Live2D Viewer</Text>
        <TouchableOpacity 
          style={styles.toggleButton}
          onPress={toggleLive2D}
        >
          <Text style={styles.toggleButtonText}>
            {isLive2DVisible ? '隐藏' : '显示'}
          </Text>
        </TouchableOpacity>
      </View>

      {/* Live2D 显示区域 */}
      {isLive2DVisible && (
        <View style={styles.live2dContainer}>
          <WebView
            ref={webViewRef}
            source={{ html: live2dHTML }}
            style={styles.webview}
            onMessage={handleWebViewMessage}
            javaScriptEnabled={true}
            domStorageEnabled={true}
            startInLoadingState={true}
            scalesPageToFit={true}
            mixedContentMode="compatibility"
          />
        </View>
      )}

      {/* 底部控制面板 */}
      <View style={styles.controlPanel}>
        <Text style={styles.panelTitle}>表情控制</Text>
        <ScrollView 
          horizontal 
          showsHorizontalScrollIndicator={false}
          style={styles.emotionScroll}
        >
          {emotions.map((emotion) => (
            <TouchableOpacity
              key={emotion.id}
              style={[
                styles.emotionButton,
                currentEmotion === emotion.id && styles.emotionButtonActive
              ]}
              onPress={() => changeEmotion(emotion.id)}
            >
              <Text style={[
                styles.emotionButtonText,
                currentEmotion === emotion.id && styles.emotionButtonTextActive
              ]}>
                {emotion.name}
              </Text>
            </TouchableOpacity>
          ))}
        </ScrollView>

        {/* 额外控制按钮 */}
        <View style={styles.extraControls}>
          <TouchableOpacity 
            style={styles.controlButton}
            onPress={() => router.push('/live2d-emotion-manager')}
          >
            <Text style={styles.controlButtonText}>表情管理</Text>
          </TouchableOpacity>
          <TouchableOpacity 
            style={styles.controlButton}
            onPress={() => router.push('/l2d-manager')}
          >
            <Text style={styles.controlButtonText}>模型管理</Text>
          </TouchableOpacity>
        </View>
      </View>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#000',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
  },
  backButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#333',
    borderRadius: 6,
  },
  backButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  title: {
    color: '#fff',
    fontSize: 18,
    fontWeight: 'bold',
  },
  toggleButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  toggleButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  live2dContainer: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  webview: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  controlPanel: {
    backgroundColor: 'rgba(0, 0, 0, 0.8)',
    padding: 16,
  },
  panelTitle: {
    color: '#fff',
    fontSize: 16,
    fontWeight: 'bold',
    marginBottom: 12,
  },
  emotionScroll: {
    marginBottom: 16,
  },
  emotionButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#333',
    borderRadius: 20,
    marginRight: 8,
  },
  emotionButtonActive: {
    backgroundColor: '#007AFF',
  },
  emotionButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  emotionButtonTextActive: {
    fontWeight: 'bold',
  },
  extraControls: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  controlButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#333',
    borderRadius: 6,
  },
  controlButtonText: {
    color: '#fff',
    fontSize: 14,
  },
});
