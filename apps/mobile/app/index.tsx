import React, { useState, useRef } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  Dimensions,
  Platform,
  Animated,
} from 'react-native';
import { router } from 'expo-router';
import { WebView } from 'react-native-webview';
import { Ionicons } from '@expo/vector-icons';

interface Message {
  id: string;
  text: string;
  isUser: boolean;
  timestamp: Date;
}

const SUBTITLE_HEIGHT = 100; // 字幕区域高度
const { width: SCREEN_WIDTH, height: SCREEN_HEIGHT } = Dimensions.get('window');

export default function HomeScreen() {
  const [messages, setMessages] = useState<Message[]>([
    {
      id: '1',
      text: '你好！我是小八，有什么可以帮助你的吗？',
      isUser: false,
      timestamp: new Date(),
    },
  ]);
  const [isSubtitleVisible, setIsSubtitleVisible] = useState(true);
  const [isDialogActive, setIsDialogActive] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const webViewRef = useRef<WebView>(null);
  const subtitleAnimation = useRef(new Animated.Value(1)).current;

  // 切换字幕显示/隐藏的动画
  const toggleSubtitle = () => {
    const toValue = isSubtitleVisible ? 0 : 1;
    Animated.timing(subtitleAnimation, {
      toValue,
      duration: 200,
      useNativeDriver: true,
    }).start();
    setIsSubtitleVisible(!isSubtitleVisible);
  };

  // 切换对话状态
  const toggleDialog = () => {
    setIsDialogActive(!isDialogActive);
    // TODO: 这里可以添加开始/结束对话的逻辑
  };

  // 切换摄像头状态
  const toggleCamera = () => {
    setIsCameraActive(!isCameraActive);
    // TODO: 这里可以添加开启/关闭摄像头的逻辑
  };

  // Live2D HTML 内容
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

  // 显示最后三条消息作为字幕
  const subtitleMessages = messages.slice(-3);

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶部工具栏 */}
      <View style={styles.header}>
        {/* 左侧按钮 */}
        <View style={styles.headerLeft}>
          <TouchableOpacity 
            style={[
              styles.actionButton,
              isDialogActive && styles.actionButtonActive
            ]}
            onPress={toggleDialog}
          >
            <Ionicons 
              name={isDialogActive ? "mic" : "mic-outline"} 
              size={20} 
              color="#fff" 
            />
            <Text style={styles.actionButtonText}>
              {isDialogActive ? '结束对话' : '开始对话'}
            </Text>
          </TouchableOpacity>

          <TouchableOpacity 
            style={[
              styles.actionButton,
              isCameraActive && styles.actionButtonActive
            ]}
            onPress={toggleCamera}
          >
            <Ionicons 
              name={isCameraActive ? "camera" : "camera-outline"} 
              size={20} 
              color="#fff" 
            />
            <Text style={styles.actionButtonText}>
              {isCameraActive ? '关闭摄像头' : '开启摄像头'}
            </Text>
          </TouchableOpacity>
        </View>

        {/* 右侧按钮 */}
        <View style={styles.headerRight}>
          <TouchableOpacity 
            style={styles.iconButton}
            onPress={toggleSubtitle}
          >
            <Ionicons 
              name={isSubtitleVisible ? "chatbox" : "chatbox-outline"} 
              size={24} 
              color="#fff" 
            />
          </TouchableOpacity>
          <TouchableOpacity 
            style={styles.iconButton}
            onPress={() => router.push('/settings')}
          >
            <Ionicons 
              name="settings-outline" 
              size={24} 
              color="#fff" 
            />
          </TouchableOpacity>
        </View>
      </View>

      {/* Live2D 显示区域 */}
      <View style={styles.live2dContainer}>
        <WebView
          ref={webViewRef}
          source={{ html: live2dHTML }}
          style={styles.webview}
          onMessage={(event) => {
            console.log('Message from WebView:', event.nativeEvent.data);
          }}
          javaScriptEnabled={true}
          domStorageEnabled={true}
          startInLoadingState={true}
          scalesPageToFit={true}
          mixedContentMode="compatibility"
        />
      </View>

      {/* 字幕区域 */}
      <Animated.View 
        style={[
          styles.subtitleContainer,
          {
            opacity: subtitleAnimation,
            transform: [{
              translateY: subtitleAnimation.interpolate({
                inputRange: [0, 1],
                outputRange: [SUBTITLE_HEIGHT, 0],
              })
            }]
          }
        ]}
      >
        {subtitleMessages.map((message) => (
          <View 
            key={message.id}
            style={[
              styles.subtitleItem,
              message.isUser ? styles.userSubtitle : styles.aiSubtitle
            ]}
          >
            <Text style={styles.subtitleText}>{message.text}</Text>
          </View>
        ))}
      </Animated.View>
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
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    position: 'absolute',
    top: Platform.OS === 'ios' ? 48 : 16,
    left: 0,
    right: 0,
    zIndex: 10,
  },
  headerLeft: {
    flexDirection: 'row',
    gap: 12,
  },
  headerRight: {
    flexDirection: 'row',
    gap: 16,
  },
  iconButton: {
    width: 40,
    height: 40,
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
    borderRadius: 20,
    justifyContent: 'center',
    alignItems: 'center',
  },
  actionButton: {
    flexDirection: 'row',
    alignItems: 'center',
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
    borderRadius: 20,
    gap: 6,
  },
  actionButtonActive: {
    backgroundColor: 'rgba(0, 122, 255, 0.6)',
  },
  actionButtonText: {
    color: '#fff',
    fontSize: 14,
    fontWeight: '500',
  },
  live2dContainer: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  webview: {
    flex: 1,
    backgroundColor: 'transparent',
  },
  subtitleContainer: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    height: SUBTITLE_HEIGHT,
    backgroundColor: 'rgba(0, 0, 0, 0.5)',
    padding: 16,
    justifyContent: 'flex-end',
  },
  subtitleItem: {
    marginBottom: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 12,
    maxWidth: '80%',
  },
  userSubtitle: {
    alignSelf: 'flex-end',
    backgroundColor: 'rgba(0, 122, 255, 0.7)',
  },
  aiSubtitle: {
    alignSelf: 'flex-start',
    backgroundColor: 'rgba(255, 255, 255, 0.2)',
  },
  subtitleText: {
    color: '#fff',
    fontSize: 16,
  },
});