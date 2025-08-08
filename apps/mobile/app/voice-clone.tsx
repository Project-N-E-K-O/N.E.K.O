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

interface VoiceModel {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
}

export default function VoiceCloneScreen() {
  const [inputText, setInputText] = useState('你好，我是小八！');
  const [isTTSEnabled, setIsTTSEnabled] = useState(true);
  const [selectedVoice, setSelectedVoice] = useState('xiaoba');
  const [speechRate, setSpeechRate] = useState(1.0);
  const [pitch, setPitch] = useState(1.0);
  const [volume, setVolume] = useState(1.0);
  const [isRecording, setIsRecording] = useState(false);

  const voiceModels: VoiceModel[] = [
    {
      id: 'xiaoba',
      name: '小八原声',
      description: '默认的小八声音模型',
      isActive: true,
    },
    {
      id: 'xiaoba_clone',
      name: '小八克隆',
      description: '基于用户声音克隆的模型',
      isActive: false,
    },
    {
      id: 'xiaoba_emotion',
      name: '小八情感',
      description: '带有情感变化的声音模型',
      isActive: false,
    },
  ];

  const speechRates = [
    { value: 0.5, label: '0.5x' },
    { value: 0.75, label: '0.75x' },
    { value: 1.0, label: '1.0x' },
    { value: 1.25, label: '1.25x' },
    { value: 1.5, label: '1.5x' },
  ];

  const pitches = [
    { value: 0.5, label: '低音' },
    { value: 0.75, label: '中低音' },
    { value: 1.0, label: '正常' },
    { value: 1.25, label: '中高音' },
    { value: 1.5, label: '高音' },
  ];

  const volumes = [
    { value: 0.3, label: '30%' },
    { value: 0.5, label: '50%' },
    { value: 0.7, label: '70%' },
    { value: 0.9, label: '90%' },
    { value: 1.0, label: '100%' },
  ];

  const startRecording = () => {
    setIsRecording(true);
    Alert.alert('录音开始', '请开始说话，录音将持续30秒');
    
    // 模拟30秒后停止录音
    setTimeout(() => {
      setIsRecording(false);
      Alert.alert('录音完成', '声音样本已录制完成，正在处理中...');
    }, 30000);
  };

  const stopRecording = () => {
    setIsRecording(false);
    Alert.alert('录音停止', '录音已停止');
  };

  const playTTS = () => {
    Alert.alert('TTS播放', `正在播放: ${inputText}`);
  };

  const saveVoiceSettings = () => {
    Alert.alert('保存成功', '语音设置已保存');
  };

  const startVoiceClone = () => {
    Alert.alert(
      '开始语音克隆',
      '需要录制您的声音样本来训练模型，确定开始吗？',
      [
        { text: '取消', style: 'cancel' },
        { text: '开始', onPress: startRecording },
      ]
    );
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
        <Text style={styles.title}>语音克隆</Text>
        <TouchableOpacity 
          style={styles.saveButton}
          onPress={saveVoiceSettings}
        >
          <Text style={styles.saveButtonText}>保存</Text>
        </TouchableOpacity>
      </View>

      <ScrollView style={styles.content}>
        {/* TTS 开关 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>TTS 设置</Text>
          </View>
          <View style={styles.settingItem}>
            <Text style={styles.settingLabel}>启用语音合成</Text>
            <Switch
              value={isTTSEnabled}
              onValueChange={setIsTTSEnabled}
              trackColor={{ false: '#767577', true: '#81b0ff' }}
              thumbColor={isTTSEnabled ? '#007AFF' : '#f4f3f4'}
            />
          </View>
        </View>

        {/* 文本输入 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>测试文本</Text>
          </View>
          <TextInput
            style={styles.textInput}
            value={inputText}
            onChangeText={setInputText}
            placeholder="输入要转换为语音的文本..."
            multiline
            numberOfLines={3}
          />
          <TouchableOpacity 
            style={styles.playButton}
            onPress={playTTS}
            disabled={!isTTSEnabled}
          >
            <Text style={styles.playButtonText}>播放</Text>
          </TouchableOpacity>
        </View>

        {/* 声音模型选择 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>声音模型</Text>
          </View>
          {voiceModels.map((model) => (
            <TouchableOpacity
              key={model.id}
              style={[
                styles.voiceModelItem,
                selectedVoice === model.id && styles.voiceModelItemActive
              ]}
              onPress={() => setSelectedVoice(model.id)}
            >
              <View style={styles.voiceModelInfo}>
                <Text style={styles.voiceModelName}>{model.name}</Text>
                <Text style={styles.voiceModelDesc}>{model.description}</Text>
              </View>
              <View style={[
                styles.voiceModelStatus,
                { backgroundColor: model.isActive ? '#34C759' : '#FF3B30' }
              ]}>
                <Text style={styles.voiceModelStatusText}>
                  {model.isActive ? '可用' : '不可用'}
                </Text>
              </View>
            </TouchableOpacity>
          ))}
        </View>

        {/* 语音参数设置 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>语音参数</Text>
          </View>
          
          {/* 语速设置 */}
          <View style={styles.paramContainer}>
            <Text style={styles.paramLabel}>语速</Text>
            <View style={styles.paramButtons}>
              {speechRates.map((rate) => (
                <TouchableOpacity
                  key={rate.value}
                  style={[
                    styles.paramButton,
                    speechRate === rate.value && styles.paramButtonActive
                  ]}
                  onPress={() => setSpeechRate(rate.value)}
                >
                  <Text style={[
                    styles.paramButtonText,
                    speechRate === rate.value && styles.paramButtonTextActive
                  ]}>
                    {rate.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* 音调设置 */}
          <View style={styles.paramContainer}>
            <Text style={styles.paramLabel}>音调</Text>
            <View style={styles.paramButtons}>
              {pitches.map((pitchOption) => (
                <TouchableOpacity
                  key={pitchOption.value}
                  style={[
                    styles.paramButton,
                    pitch === pitchOption.value && styles.paramButtonActive
                  ]}
                  onPress={() => setPitch(pitchOption.value)}
                >
                  <Text style={[
                    styles.paramButtonText,
                    pitch === pitchOption.value && styles.paramButtonTextActive
                  ]}>
                    {pitchOption.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>

          {/* 音量设置 */}
          <View style={styles.paramContainer}>
            <Text style={styles.paramLabel}>音量</Text>
            <View style={styles.paramButtons}>
              {volumes.map((vol) => (
                <TouchableOpacity
                  key={vol.value}
                  style={[
                    styles.paramButton,
                    volume === vol.value && styles.paramButtonActive
                  ]}
                  onPress={() => setVolume(vol.value)}
                >
                  <Text style={[
                    styles.paramButtonText,
                    volume === vol.value && styles.paramButtonTextActive
                  ]}>
                    {vol.label}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </View>
        </View>

        {/* 语音克隆 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>语音克隆</Text>
          </View>
          <Text style={styles.cloneDescription}>
            录制您的声音样本来创建个性化的语音模型
          </Text>
          
          <View style={styles.cloneButtons}>
            <TouchableOpacity 
              style={[
                styles.recordButton,
                isRecording && styles.recordButtonActive
              ]}
              onPress={isRecording ? stopRecording : startVoiceClone}
            >
              <Text style={styles.recordButtonText}>
                {isRecording ? '停止录音' : '开始克隆'}
              </Text>
            </TouchableOpacity>
            
            <TouchableOpacity 
              style={styles.infoButton}
              onPress={() => Alert.alert('说明', '需要录制至少30秒的清晰语音样本')}
            >
              <Text style={styles.infoButtonText}>使用说明</Text>
            </TouchableOpacity>
          </View>
        </View>

        {/* 状态信息 */}
        <View style={styles.section}>
          <View style={styles.sectionHeader}>
            <Text style={styles.sectionTitle}>状态信息</Text>
          </View>
          <View style={styles.statusItem}>
            <Text style={styles.statusLabel}>当前模型:</Text>
            <Text style={styles.statusValue}>
              {voiceModels.find(m => m.id === selectedVoice)?.name}
            </Text>
          </View>
          <View style={styles.statusItem}>
            <Text style={styles.statusLabel}>语速:</Text>
            <Text style={styles.statusValue}>{speechRate}x</Text>
          </View>
          <View style={styles.statusItem}>
            <Text style={styles.statusLabel}>音调:</Text>
            <Text style={styles.statusValue}>{pitch}x</Text>
          </View>
          <View style={styles.statusItem}>
            <Text style={styles.statusLabel}>音量:</Text>
            <Text style={styles.statusValue}>{(volume * 100).toFixed(0)}%</Text>
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
  playButton: {
    backgroundColor: '#007AFF',
    paddingHorizontal: 16,
    paddingVertical: 8,
    borderRadius: 6,
    alignSelf: 'flex-end',
    marginTop: 8,
  },
  playButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  voiceModelItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    padding: 12,
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    marginBottom: 8,
  },
  voiceModelItemActive: {
    borderColor: '#007AFF',
    backgroundColor: '#f0f8ff',
  },
  voiceModelInfo: {
    flex: 1,
  },
  voiceModelName: {
    fontSize: 16,
    fontWeight: '500',
    color: '#333',
  },
  voiceModelDesc: {
    fontSize: 14,
    color: '#666',
    marginTop: 4,
  },
  voiceModelStatus: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  voiceModelStatusText: {
    fontSize: 12,
    color: '#fff',
    fontWeight: '500',
  },
  paramContainer: {
    marginBottom: 16,
  },
  paramLabel: {
    fontSize: 16,
    color: '#333',
    marginBottom: 8,
  },
  paramButtons: {
    flexDirection: 'row',
    flexWrap: 'wrap',
  },
  paramButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#f0f0f0',
    borderRadius: 16,
    marginRight: 8,
    marginBottom: 8,
  },
  paramButtonActive: {
    backgroundColor: '#007AFF',
  },
  paramButtonText: {
    fontSize: 14,
    color: '#333',
  },
  paramButtonTextActive: {
    color: '#fff',
  },
  cloneDescription: {
    fontSize: 14,
    color: '#666',
    marginBottom: 16,
    lineHeight: 20,
  },
  cloneButtons: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
  recordButton: {
    flex: 1,
    backgroundColor: '#007AFF',
    paddingVertical: 12,
    borderRadius: 8,
    marginRight: 8,
    alignItems: 'center',
  },
  recordButtonActive: {
    backgroundColor: '#FF3B30',
  },
  recordButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
  infoButton: {
    paddingHorizontal: 16,
    paddingVertical: 12,
    backgroundColor: '#f0f0f0',
    borderRadius: 8,
  },
  infoButtonText: {
    color: '#333',
    fontSize: 14,
  },
  statusItem: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: 8,
  },
  statusLabel: {
    fontSize: 14,
    color: '#666',
  },
  statusValue: {
    fontSize: 14,
    color: '#333',
    fontWeight: '500',
  },
});
