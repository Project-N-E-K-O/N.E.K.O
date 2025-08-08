import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  Switch,
  TextInput,
  Alert,
} from 'react-native';
import { router } from 'expo-router';

export default function SubtitleScreen() {
  const [isSubtitleEnabled, setIsSubtitleEnabled] = useState(true);
  const [subtitleText, setSubtitleText] = useState('欢迎使用小八！');
  const [fontSize, setFontSize] = useState(16);
  const [textColor, setTextColor] = useState('#FFFFFF');
  const [backgroundColor, setBackgroundColor] = useState('#000000');
  const [opacity, setOpacity] = useState(0.8);
  const [position, setPosition] = useState('bottom'); // top, center, bottom

  const colors = [
    { name: '白色', value: '#FFFFFF' },
    { name: '黄色', value: '#FFFF00' },
    { name: '绿色', value: '#00FF00' },
    { name: '蓝色', value: '#00FFFF' },
    { name: '红色', value: '#FF0000' },
    { name: '橙色', value: '#FFA500' },
  ];

  const positions = [
    { id: 'top', name: '顶部' },
    { id: 'center', name: '中央' },
    { id: 'bottom', name: '底部' },
  ];

  const saveSettings = () => {
    Alert.alert('保存成功', '字幕设置已保存');
  };

  const resetSettings = () => {
    Alert.alert(
      '重置设置',
      '确定要重置所有字幕设置吗？',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '重置',
          style: 'destructive',
          onPress: () => {
            setFontSize(16);
            setTextColor('#FFFFFF');
            setBackgroundColor('#000000');
            setOpacity(0.8);
            setPosition('bottom');
          },
        },
      ]
    );
  };

  const previewSubtitle = () => {
    Alert.alert('预览', `当前字幕: ${subtitleText}`);
  };

  return (
    <SafeAreaView style={styles.container}>
      {/* 顶部导航 */}
      <View style={styles.header}>
        <TouchableOpacity 
          style={styles.backButton}
          onPress={() => router.back()}
        >
          <Text style={styles.backButtonText}>返回</Text>
        </TouchableOpacity>
        <Text style={styles.title}>字幕管理</Text>
        <TouchableOpacity 
          style={styles.saveButton}
          onPress={saveSettings}
        >
          <Text style={styles.saveButtonText}>保存</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.content}>
        {/* 开关设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>基本设置</Text>
          </View>
          <View style={styles.settingItem}>
            <Text style={styles.settingLabel}>启用字幕</Text>
            <Switch
              value={isSubtitleEnabled}
              onValueChange={setIsSubtitleEnabled}
              trackColor={{ false: '#767577', true: '#81b0ff' }}
              thumbColor={isSubtitleEnabled ? '#007AFF' : '#f4f3f4'}
            />
          </View>
        </View>

        {/* 字幕内容 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>字幕内容</Text>
          </View>
          <TextInput
            style={styles.textInput}
            value={subtitleText}
            onChangeText={setSubtitleText}
            placeholder="输入字幕内容..."
            multiline
            numberOfLines={3}
          />
          <TouchableOpacity 
            style={styles.previewButton}
            onPress={previewSubtitle}
          >
            <Text style={styles.previewButtonText}>预览</Text>
          </TouchableOpacity>
        </View>

        {/* 字体大小 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>字体大小</Text>
          </View>
          <View style={styles.fontSizeContainer}>
            <TouchableOpacity
              style={styles.fontSizeButton}
              onPress={() => setFontSize(Math.max(12, fontSize - 2))}
            >
              <Text style={styles.fontSizeButtonText}>A-</Text>
            </TouchableOpacity>
            <Text style={styles.fontSizeText}>{fontSize}px</Text>
            <TouchableOpacity
              style={styles.fontSizeButton}
              onPress={() => setFontSize(Math.min(32, fontSize + 2))}
            >
              <Text style={styles.fontSizeButtonText}>A+</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* 文字颜色 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>文字颜色</Text>
          </View>
          <View style={styles.colorGrid}>
            {colors.map((color) => (
              <TouchableOpacity
                key={color.value}
                style={[
                  styles.colorButton,
                  { backgroundColor: color.value },
                  textColor === color.value && styles.colorButtonActive
                ]}
                onPress={() => setTextColor(color.value)}
              >
                <Text style={styles.colorName}>{color.name}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 背景颜色 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>背景颜色</Text>
          </View>
          <View style={styles.colorGrid}>
            {colors.map((color) => (
              <TouchableOpacity
                key={color.value}
                style={[
                  styles.colorButton,
                  { backgroundColor: color.value },
                  backgroundColor === color.value && styles.colorButtonActive
                ]}
                onPress={() => setBackgroundColor(color.value)}
              >
                <Text style={styles.colorName}>{color.name}</Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 透明度 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>透明度</Text>
          </View>
          <View style={styles.opacityContainer}>
            <Text style={styles.opacityText}>{(opacity * 100).toFixed(0)}%</Text>
            <View style={styles.opacitySlider}>
              {[0.1, 0.3, 0.5, 0.7, 0.9, 1.0].map((value) => (
                <TouchableOpacity
                  key={value}
                  style={[
                    styles.opacityButton,
                    opacity === value && styles.opacityButtonActive
                  ]}
                  onPress={() => setOpacity(value)}
                >
                  <Text style={styles.opacityButtonText}>
                    {(value * 100).toFixed(0)}%
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        </View>

        {/* 位置设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>显示位置</Text>
          </View>
          <View style={styles.positionContainer}>
            {positions.map((pos) => (
              <TouchableOpacity
                key={pos.id}
                style={[
                  styles.positionButton,
                  position === pos.id && styles.positionButtonActive
                ]}
                onPress={() => setPosition(pos.id)}
              >
                <Text style={[
                  styles.positionButtonText,
                  position === pos.id && styles.positionButtonTextActive
                ]}>
                  {pos.name}
                </Text>
              </TouchableOpacity>
            ))}
          </View>
        </View>

        {/* 操作按钮 */}
        <View style={styles.section}>
          <View style={styles.buttonContainer}>
            <TouchableOpacity 
              style={styles.resetButton}
              onPress={resetSettings}
            >
              <Text style={styles.resetButtonText}>重置设置</Text>
            </TouchableOpacity>
          </View>
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  container: {
    flex: 1,
    backgroundColor: '#f7f8fa',
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e1e5e9',
  },
  backButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
  },
  backButtonText: {
    color: '#333',
    fontSize: 14,
  },
  title: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#333',
  },
  saveButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  saveButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  content: {
    flex: 1,
  },
  section: {
    backgroundColor: '#fff',
    margin: 8,
    borderRadius: 12,
    padding: 16,
  },
  sectionHeader: {
    marginBottom: 16,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
  },
  settingItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  settingLabel: {
    fontSize: 16,
    color: '#333',
  },
  textInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    minHeight: 80,
    textAlignVertical: 'top',
  },
  previewButton: {
    backgroundColor: '#007AFF',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
    alignSelf: 'flex-end',
    marginTop: 8,
  },
  previewButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  fontSizeContainer: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
  },
  fontSizeButton: {
    backgroundColor: '#f0f0f0',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
  },
  fontSizeButtonText: {
    fontSize: 18,
    fontWeight: 'bold',
    color: '#333',
  },
  fontSizeText: {
    fontSize: 16,
    marginHorizontal: 20,
    color: '#333',
  },
  colorGrid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
  },
  colorButton: {
    width: '30%',
    paddingVertical: 12,
    borderRadius: 8,
    marginBottom: 8,
    alignItems: 'center',
    borderWidth: 2,
    borderColor: 'transparent',
  },
  colorButtonActive: {
    borderColor: '#007AFF',
  },
  colorName: {
    fontSize: 12,
    fontWeight: 'bold',
    color: '#333',
  },
  opacityContainer: {
    alignItems: 'center',
  },
  opacityText: {
    fontSize: 16,
    color: '#333',
    marginBottom: 12,
  },
  opacitySlider: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'center',
  },
  opacityButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#f0f0f0',
    borderRadius: 16,
    marginHorizontal: 4,
    marginBottom: 8,
  },
  opacityButtonActive: {
    backgroundColor: '#007AFF',
  },
  opacityButtonText: {
    fontSize: 12,
    color: '#333',
  },
  positionContainer: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  positionButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
  },
  positionButtonActive: {
    backgroundColor: '#007AFF',
  },
  positionButtonText: {
    fontSize: 14,
    color: '#333',
  },
  positionButtonTextActive: {
    color: '#fff',
  },
  buttonContainer: {
    alignItems: 'center',
  },
  resetButton: {
    backgroundColor: '#FF3B30',
    paddingHorizontal: 24,
    paddingVertical: 12,
    borderRadius: 8,
  },
  resetButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
});
