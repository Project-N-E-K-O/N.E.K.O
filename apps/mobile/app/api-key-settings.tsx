import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  TextInput,
  Alert,
  Switch,
} from 'react-native';
import { router } from 'expo-router';

interface ApiKey {
  id: string;
  name: string;
  key: string;
  isActive: boolean;
  description: string;
}

export default function ApiKeySettingsScreen() {
  const [apiKeys, setApiKeys] = useState<ApiKey[]>([
    {
      id: 'gemini',
      name: 'Gemini API',
      key: '',
      isActive: false,
      description: 'Google Gemini AI API密钥',
    },
    {
      id: 'openai',
      name: 'OpenAI API',
      key: '',
      isActive: false,
      description: 'OpenAI GPT API密钥',
    },
    {
      id: 'azure',
      name: 'Azure OpenAI',
      key: '',
      isActive: false,
      description: 'Azure OpenAI服务密钥',
    },
    {
      id: 'claude',
      name: 'Claude API',
      key: '',
      isActive: false,
      description: 'Anthropic Claude API密钥',
    },
  ]);

  const [serverUrl, setServerUrl] = useState('http://localhost:8000');
  const [isAutoConnect, setIsAutoConnect] = useState(true);
  const [connectionStatus, setConnectionStatus] = useState('disconnected');

  const updateApiKey = (id: string, key: string) => {
    setApiKeys(apiKeys.map(api => 
      api.id === id ? { ...api, key } : api
    ));
  };

  const toggleApiKey = (id: string) => {
    setApiKeys(apiKeys.map(api => 
      api.id === id ? { ...api, isActive: !api.isActive } : api
    ));
  };

  const testConnection = (id: string) => {
    const apiKey = apiKeys.find(api => api.id === id);
    if (!apiKey || !apiKey.key) {
      Alert.alert('错误', '请先输入API密钥');
      return;
    }

    Alert.alert('测试连接', `正在测试 ${apiKey.name} 连接...`);
    // 这里可以添加实际的API测试逻辑
  };

  const saveAllSettings = () => {
    Alert.alert('保存成功', '所有设置已保存');
  };

  const resetAllSettings = () => {
    Alert.alert(
      '重置设置',
      '确定要重置所有API设置吗？这将清除所有API密钥。',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '重置',
          style: 'destructive',
          onPress: () => {
            setApiKeys(apiKeys.map(api => ({ ...api, key: '', isActive: false })));
            setServerUrl('http://localhost:8000');
            setIsAutoConnect(true);
          },
        },
      ]
    );
  };

  const testServerConnection = () => {
    setConnectionStatus('connecting');
    Alert.alert('连接测试', '正在测试服务器连接...');
    
    // 模拟连接测试
    setTimeout(() => {
      setConnectionStatus('connected');
      Alert.alert('连接成功', '服务器连接正常');
    }, 2000);
  };

  const getStatusColor = () => {
    switch (connectionStatus) {
      case 'connected': return '#34C759';
      case 'connecting': return '#FF9500';
      case 'disconnected': return '#FF3B30';
      default: return '#8E8E93';
    }
  };

  const getStatusText = () => {
    switch (connectionStatus) {
      case 'connected': return '已连接';
      case 'connecting': return '连接中...';
      case 'disconnected': return '未连接';
      default: return '未知';
    }
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
        <Text style={styles.title}>API设置</Text>
        <TouchableOpacity 
          style={styles.saveButton}
          onPress={saveAllSettings}
        >
          <Text style={styles.saveButtonText}>保存</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.content}>
        {/* 服务器连接设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>服务器连接</Text>
          </View>
          
          <View style={styles.serverContainer}>
            <Text style={styles.settingLabel}>服务器地址</Text>
            <TextInput
              style={styles.serverInput}
              value={serverUrl}
              onChangeText={setServerUrl}
              placeholder="http://localhost:8000"
            />
            
            <View style={styles.connectionStatus}>
              <View style={[styles.statusIndicator, { backgroundColor: getStatusColor() }]} />
              <Text style={styles.statusText}>{getStatusText()}</Text>
            </View>
            
            <TouchableOpacity 
              style={styles.testButton}
              onPress={testServerConnection}
            >
              <Text style={styles.testButtonText}>测试连接</Text>
            </TouchableOpacity>
          </View>

          <View style={styles.settingItem}>
            <Text style={styles.settingLabel}>自动连接</Text>
            <Switch
              value={isAutoConnect}
              onValueChange={setIsAutoConnect}
              trackColor={{ false: '#767577', true: '#81b0ff' }}
              thumbColor={isAutoConnect ? '#007AFF' : '#f4f3f4'}
            />
          </View>
        </View>

        {/* API密钥设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>API密钥</Text>
          </View>
          
          {apiKeys.map((apiKey) => (
            <View key={apiKey.id} style={styles.apiKeyContainer}>
              <View style={styles.apiKeyHeader}>
                <View style={styles.apiKeyInfo}>
                  <Text style={styles.apiKeyName}>{apiKey.name}</Text>
                  <Text style={styles.apiKeyDesc}>{apiKey.description}</Text>
                </View>
                <View style={styles.apiKeyControls}>
                  <Switch
                    value={apiKey.isActive}
                    onValueChange={() => toggleApiKey(apiKey.id)}
                    trackColor={{ false: '#767577', true: '#81b0ff' }}
                    thumbColor={apiKey.isActive ? '#007AFF' : '#f4f3f4'}
                  />
                </View>
              </View>
              
              <TextInput
                style={styles.apiKeyInput}
                value={apiKey.key}
                onChangeText={(text) => updateApiKey(apiKey.id, text)}
                placeholder={`输入${apiKey.name}密钥`}
                secureTextEntry
              />
              
              <TouchableOpacity 
                style={styles.testApiButton}
                onPress={() => testConnection(apiKey.id)}
              >
                <Text style={styles.testApiButtonText}>测试连接</Text>
              </TouchableOpacity>
            </View>
          ))}
        </View>

        {/* 高级设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>高级设置</Text>
          </View>
          
          <View style={styles.advancedSetting}>
            <Text style={styles.settingLabel}>请求超时时间</Text>
            <TextInput
              style={styles.numberInput}
              placeholder="30"
              keyboardType="numeric"
            />
            <Text style={styles.unitText}>秒</Text>
          </View>
          
          <View style={styles.advancedSetting}>
            <Text style={styles.settingLabel}>最大重试次数</Text>
            <TextInput
              style={styles.numberInput}
              placeholder="3"
              keyboardType="numeric"
            />
            <Text style={styles.unitText}>次</Text>
          </View>
          
          <View style={styles.settingItem}>
            <Text style={styles.settingLabel}>启用日志记录</Text>
            <Switch
              value={true}
              onValueChange={() => {}}
              trackColor={{ false: '#767577', true: '#81b0ff' }}
              thumbColor={'#007AFF'}
            />
          </View>
        </View>

        {/* 操作按钮 */}
        <View style={styles.section}>
          <View style={styles.buttonContainer}>
            <TouchableOpacity 
              style={styles.resetButton}
              onPress={resetAllSettings}
            >
              <Text style={styles.resetButtonText}>重置所有设置</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* 使用说明 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>使用说明</Text>
          </View>
          <Text style={styles.instructionText}>
            1. 首先配置服务器连接地址{'\n'}
            2. 输入相应的API密钥{'\n'}
            3. 启用需要使用的API服务{'\n'}
            4. 测试连接确保配置正确{'\n'}
            5. 保存设置开始使用
          </Text>
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
  serverContainer: {
    marginBottom: 16,
  },
  settingLabel: {
    fontSize: 16,
    color: '#333',
    marginBottom: 8,
  },
  serverInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    marginBottom: 8,
  },
  connectionStatus: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 8,
  },
  statusIndicator: {
    width: 12,
    height: 12,
    borderRadius: 6,
    marginRight: 8,
  },
  statusText: {
    fontSize: 14,
    color: '#666',
  },
  testButton: {
    backgroundColor: '#007AFF',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
    alignSelf: 'flex-start',
  },
  testButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  settingItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 16,
  },
  apiKeyContainer: {
    marginBottom: 16,
    padding: 12,
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
  },
  apiKeyHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  apiKeyInfo: {
    flex: 1,
  },
  apiKeyName: {
    fontSize: 16,
    fontWeight: '500',
    color: '#333',
  },
  apiKeyDesc: {
    fontSize: 14,
    color: '#666',
    marginTop: 2,
  },
  apiKeyControls: {
    marginLeft: 8,
  },
  apiKeyInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    marginBottom: 8,
  },
  testApiButton: {
    backgroundColor: '#f0f0f0',
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 6,
    alignSelf: 'flex-start',
  },
  testApiButtonText: {
    color: '#333',
    fontSize: 14,
  },
  advancedSetting: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  numberInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 6,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    width: 80,
    marginLeft: 8,
  },
  unitText: {
    fontSize: 16,
    color: '#666',
    marginLeft: 8,
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
  instructionText: {
    fontSize: 14,
    color: '#666',
    lineHeight: 20,
  },
});
