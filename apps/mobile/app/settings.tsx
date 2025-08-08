import React from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
} from 'react-native';
import { router } from 'expo-router';

interface SettingItem {
  id: string;
  title: string;
  description: string;
  route: string;
  icon?: string; // 可以后续添加图标
}

export default function SettingsScreen() {
  const settingItems: SettingItem[] = [
    {
      id: 'live2d',
      title: '虚拟形象',
      description: '更换和管理Live2D模型',
      route: '/l2d-manager',
    },
    {
      id: 'emotion',
      title: '表情管理',
      description: '管理Live2D模型的表情和动作',
      route: '/live2d-emotion-manager',
    },
    {
      id: 'subtitle',
      title: '字幕设置',
      description: '调整字幕显示样式和位置',
      route: '/subtitle',
    },
    {
      id: 'voice',
      title: '语音设置',
      description: '语音合成和克隆配置',
      route: '/voice-clone',
    },
    {
      id: 'memory',
      title: '记忆管理',
      description: '浏览和管理AI的记忆数据',
      route: '/memory-browser',
    },
    {
      id: 'character',
      title: '角色管理',
      description: '管理AI角色设定',
      route: '/chara-manager',
    },
    {
      id: 'api',
      title: 'API设置',
      description: '配置服务器和API密钥',
      route: '/api-key-settings',
    },
  ];

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
        <Text style={styles.title}>设置</Text>
        <View style={styles.placeholder} />
      </View>

      {/* 设置列表 */}
      <ScrollView style={styles.content}>
        {settingItems.map((item) => (
          <TouchableOpacity
            key={item.id}
            style={styles.settingItem}
            onPress={() => router.push(item.route)}
          >
            <View style={styles.settingContent}>
              <Text style={styles.settingTitle}>{item.title}</Text>
              <Text style={styles.settingDescription}>{item.description}</Text>
            </View>
            <Text style={styles.settingArrow}>›</Text>
          </TouchableOpacity>
        ))}
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
  placeholder: {
    width: 50,
  },
  content: {
    flex: 1,
  },
  settingItem: {
    flexDirection: 'row',
    alignItems: 'center',
    padding: 16,
    backgroundColor: '#fff',
    borderBottomWidth: 1,
    borderBottomColor: '#e1e5e9',
  },
  settingContent: {
    flex: 1,
  },
  settingTitle: {
    fontSize: 16,
    fontWeight: '500',
    color: '#333',
    marginBottom: 4,
  },
  settingDescription: {
    fontSize: 14,
    color: '#666',
  },
  settingArrow: {
    fontSize: 20,
    color: '#999',
    marginLeft: 8,
  },
});
