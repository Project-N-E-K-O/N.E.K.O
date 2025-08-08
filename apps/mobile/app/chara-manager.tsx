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
  FlatList,
} from 'react-native';
import { router } from 'expo-router';

interface Character {
  id: string;
  name: string;
  description: string;
  personality: string;
  isActive: boolean;
  createdAt: string;
}

export default function CharaManagerScreen() {
  const [characters, setCharacters] = useState<Character[]>([
    {
      id: '1',
      name: '小八',
      description: '一个可爱的AI助手，性格活泼开朗',
      personality: '我是一个活泼可爱的AI助手，喜欢帮助用户解决问题，说话风格轻松友好。',
      isActive: true,
      createdAt: '2024-01-15',
    },
    {
      id: '2',
      name: '小八-专业版',
      description: '专业严谨的AI助手，适合工作场景',
      personality: '我是一个专业的AI助手，擅长分析和解决问题，回答准确严谨。',
      isActive: false,
      createdAt: '2024-01-16',
    },
    {
      id: '3',
      name: '小八-幽默版',
      description: '幽默风趣的AI助手，善于调节气氛',
      personality: '我是一个幽默的AI助手，喜欢开玩笑，能够活跃气氛，让对话更加有趣。',
      isActive: false,
      createdAt: '2024-01-17',
    },
  ]);

  const [selectedCharacter, setSelectedCharacter] = useState<Character | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDescription, setEditDescription] = useState('');
  const [editPersonality, setEditPersonality] = useState('');

  const activateCharacter = (id: string) => {
    setCharacters(characters.map(char => ({
      ...char,
      isActive: char.id === id
    })));
    Alert.alert('激活成功', '角色已激活');
  };

  const deleteCharacter = (id: string) => {
    Alert.alert(
      '确认删除',
      '确定要删除这个角色吗？此操作不可恢复。',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '删除',
          style: 'destructive',
          onPress: () => {
            setCharacters(characters.filter(char => char.id !== id));
            if (selectedCharacter?.id === id) {
              setSelectedCharacter(null);
            }
          },
        },
      ]
    );
  };

  const startEditing = (character: Character) => {
    setSelectedCharacter(character);
    setEditName(character.name);
    setEditDescription(character.description);
    setEditPersonality(character.personality);
    setIsEditing(true);
  };

  const saveEdit = () => {
    if (!selectedCharacter) return;
    
    setCharacters(characters.map(char => 
      char.id === selectedCharacter.id 
        ? { ...char, name: editName, description: editDescription, personality: editPersonality }
        : char
    ));
    setIsEditing(false);
    setSelectedCharacter(null);
    Alert.alert('保存成功', '角色信息已更新');
  };

  const cancelEdit = () => {
    setIsEditing(false);
    setSelectedCharacter(null);
  };

  const createNewCharacter = () => {
    const newCharacter: Character = {
      id: Date.now().toString(),
      name: '新角色',
      description: '角色描述',
      personality: '角色性格设定',
      isActive: false,
      createdAt: new Date().toISOString().split('T')[0],
    };
    setCharacters([...characters, newCharacter]);
    setSelectedCharacter(newCharacter);
    setEditName(newCharacter.name);
    setEditDescription(newCharacter.description);
    setEditPersonality(newCharacter.personality);
    setIsEditing(true);
  };

  const renderCharacterItem = ({ item }: { item: Character }) => (
    <TouchableOpacity
      style={[
        styles.characterItem,
        selectedCharacter?.id === item.id && styles.characterItemSelected
      ]}
      onPress={() => setSelectedCharacter(item)}
    >
      <View style={styles.characterHeader}>
        <Text style={styles.characterName}>{item.name}</Text>
        <View style={[
          styles.statusBadge,
          { backgroundColor: item.isActive ? '#34C759' : '#8E8E93' }
        ]}>
          <Text style={styles.statusText}>
            {item.isActive ? '活跃' : '非活跃'}
          </Text>
        </View>
      </View>
      <Text style={styles.characterDesc}>{item.description}</Text>
      <Text style={styles.characterDate}>创建于: {item.createdAt}</Text>
    </TouchableOpacity>
  );

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
        <Text style={styles.title}>角色管理</Text>
        <TouchableOpacity 
          style={styles.newButton}
          onPress={createNewCharacter}
        >
          <Text style={styles.newButtonText}>新建</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.content}>
        {/* 角色列表 */}
        <View style={styles.listSection}>
          <Text style={styles.sectionTitle}>角色列表</Text>
          <FlatList
            data={characters}
            renderItem={renderCharacterItem}
            keyExtractor={(item) => item.id}
            style={styles.characterList}
            showsVerticalScrollIndicator={false}
          />
        </View>

        {/* 角色详情 */}
        {selectedCharacter && (
          <View style={styles.detailSection}>
            <Text style={styles.sectionTitle}>角色详情</Text>
            
            {isEditing ? (
              <View style={styles.editForm}>
                <Text style={styles.formLabel}>角色名称</Text>
                <TextInput
                  style={styles.formInput}
                  value={editName}
                  onChangeText={setEditName}
                  placeholder="输入角色名称"
                />
                
                <Text style={styles.formLabel}>角色描述</Text>
                <TextInput
                  style={styles.formInput}
                  value={editDescription}
                  onChangeText={setEditDescription}
                  placeholder="输入角色描述"
                  multiline
                />
                
                <Text style={styles.formLabel}>性格设定</Text>
                <TextInput
                  style={[styles.formInput, styles.textArea]}
                  value={editPersonality}
                  onChangeText={setEditPersonality}
                  placeholder="输入角色性格设定"
                  multiline
                  numberOfLines={4}
                />
                
                <View style={styles.editButtons}>
                  <TouchableOpacity 
                    style={styles.saveButton}
                    onPress={saveEdit}
                  >
                    <Text style={styles.saveButtonText}>保存</Text>
                  </TouchableOpacity>
                  <TouchableOpacity 
                    style={styles.cancelButton}
                    onPress={cancelEdit}
                  >
                    <Text style={styles.cancelButtonText}>取消</Text>
                  </TouchableOpacity>
                </View>
              </View>
            ) : (
              <View style={styles.characterDetail}>
                <Text style={styles.detailName}>{selectedCharacter.name}</Text>
                <Text style={styles.detailDesc}>{selectedCharacter.description}</Text>
                <Text style={styles.detailPersonality}>{selectedCharacter.personality}</Text>
                
                <View style={styles.actionButtons}>
                  <TouchableOpacity 
                    style={styles.actionButton}
                    onPress={() => startEditing(selectedCharacter)}
                  >
                    <Text style={styles.actionButtonText}>编辑</Text>
                  </TouchableOpacity>
                  
                  {!selectedCharacter.isActive && (
                    <TouchableOpacity 
                      style={[styles.actionButton, styles.activateButton]}
                      onPress={() => activateCharacter(selectedCharacter.id)}
                    >
                      <Text style={styles.actionButtonText}>激活</Text>
                    </TouchableOpacity>
                  )}
                  
                  <TouchableOpacity 
                    style={[styles.actionButton, styles.deleteButton]}
                    onPress={() => deleteCharacter(selectedCharacter.id)}
                  >
                    <Text style={styles.actionButtonText}>删除</Text>
                  </TouchableOpacity>
                </View>
              </View>
            )}
          </View>
        )}
      </View>
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
  newButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  newButtonText: {
    color: '#fff',
    fontSize: 14,
  },
  content: {
    flex: 1,
    flexDirection: 'row',
  },
  listSection: {
    flex: 1,
    padding: 16,
  },
  detailSection: {
    flex: 1,
    padding: 16,
    backgroundColor: '#fff',
    margin: 8,
    borderRadius: 12,
  },
  sectionTitle: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
    marginBottom: 12,
  },
  characterList: {
    flex: 1,
  },
  characterItem: {
    backgroundColor: '#fff',
    padding: 16,
    borderRadius: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e1e5e9',
  },
  characterItemSelected: {
    borderColor: '#007AFF',
    backgroundColor: '#f0f8ff',
  },
  characterHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  characterName: {
    fontSize: 16,
    fontWeight: 'bold',
    color: '#333',
  },
  statusBadge: {
    paddingHorizontal: 8,
    paddingVertical: 4,
    borderRadius: 12,
  },
  statusText: {
    color: '#fff',
    fontSize: 12,
    fontWeight: '500',
  },
  characterDesc: {
    fontSize: 14,
    color: '#666',
    marginBottom: 8,
  },
  characterDate: {
    fontSize: 12,
    color: '#999',
  },
  characterDetail: {
    flex: 1,
  },
  detailName: {
    fontSize: 20,
    fontWeight: 'bold',
    color: '#333',
    marginBottom: 12,
  },
  detailDesc: {
    fontSize: 16,
    color: '#666',
    marginBottom: 16,
    lineHeight: 22,
  },
  detailPersonality: {
    fontSize: 14,
    color: '#333',
    backgroundColor: '#f8f9fa',
    padding: 12,
    borderRadius: 8,
    marginBottom: 20,
    lineHeight: 20,
  },
  actionButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
  },
  actionButton: {
    paddingHorizontal: 16,
    paddingVertical: 8,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
  },
  activateButton: {
    backgroundColor: '#34C759',
  },
  deleteButton: {
    backgroundColor: '#FF3B30',
  },
  actionButtonText: {
    color: '#333',
    fontSize: 14,
    fontWeight: '500',
  },
  editForm: {
    flex: 1,
  },
  formLabel: {
    fontSize: 16,
    fontWeight: '500',
    color: '#333',
    marginBottom: 8,
  },
  formInput: {
    borderWidth: 1,
    borderColor: '#e1e5e9',
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 8,
    fontSize: 16,
    marginBottom: 16,
  },
  textArea: {
    minHeight: 100,
    textAlignVertical: 'top',
  },
  editButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    marginTop: 20,
  },
  saveButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#007AFF',
    borderRadius: 8,
  },
  saveButtonText: {
    color: '#fff',
    fontSize: 16,
    fontWeight: '500',
  },
  cancelButton: {
    paddingHorizontal: 20,
    paddingVertical: 10,
    backgroundColor: '#f0f0f0',
    borderRadius: 8,
  },
  cancelButtonText: {
    color: '#333',
    fontSize: 16,
    fontWeight: '500',
  },
});
