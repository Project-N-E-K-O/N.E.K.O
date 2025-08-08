import React, { useState } from 'react';
import {
  View,
  Text,
  TouchableOpacity,
  StyleSheet,
  SafeAreaView,
  ScrollView,
  Alert,
  FlatList,
} from 'react-native';
import { router } from 'expo-router';

interface Live2DModel {
  id: string;
  name: string;
  description: string;
  isActive: boolean;
  fileSize: string;
  lastModified: string;
  hasPhysics: boolean;
  hasExpressions: boolean;
}

export default function L2DManagerScreen() {
  const [models, setModels] = useState<Live2DModel[]>([
    {
      id: '1',
      name: '小八模型',
      description: '默认的小八Live2D模型',
      isActive: true,
      fileSize: '15.2 MB',
      lastModified: '2024-01-15',
      hasPhysics: true,
      hasExpressions: true,
    },
    {
      id: '2',
      name: '小八-可爱版',
      description: '更加可爱的小八模型',
      isActive: false,
      fileSize: '18.7 MB',
      lastModified: '2024-01-16',
      hasPhysics: true,
      hasExpressions: true,
    },
    {
      id: '3',
      name: '小八-成熟版',
      description: '成熟稳重的小八模型',
      isActive: false,
      fileSize: '16.8 MB',
      lastModified: '2024-01-17',
      hasPhysics: false,
      hasExpressions: true,
    },
  ]);

  const [selectedModel, setSelectedModel] = useState<Live2DModel | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const activateModel = (id: string) => {
    setModels(models.map(model => ({
      ...model,
      isActive: model.id === id
    })));
    Alert.alert('激活成功', '模型已激活');
  };

  const deleteModel = (id: string) => {
    Alert.alert(
      '确认删除',
      '确定要删除这个模型吗？此操作不可恢复。',
      [
        { text: '取消', style: 'cancel' },
        {
          text: '删除',
          style: 'destructive',
          onPress: () => {
            setModels(models.filter(model => model.id !== id));
            if (selectedModel?.id === id) {
              setSelectedModel(null);
            }
          },
        },
      ]
    );
  };

  const importModel = () => {
    Alert.alert('导入模型', '请选择要导入的Live2D模型文件');
    // 这里可以添加文件选择逻辑
  };

  const exportModel = (model: Live2DModel) => {
    Alert.alert('导出模型', `正在导出 ${model.name}...`);
  };

  const previewModel = (model: Live2DModel) => {
    Alert.alert('预览模型', `正在加载 ${model.name} 进行预览...`);
  };

  const renderModelItem = ({ item }: { item: Live2DModel }) => (
    <TouchableOpacity
      style={[
        styles.modelItem,
        selectedModel?.id === item.id && styles.modelItemSelected
      ]}
      onPress={() => setSelectedModel(item)}
    >
      <View style={styles.modelHeader}>
        <Text style={styles.modelName}>{item.name}</Text>
        <View style={[
          styles.statusBadge,
          { backgroundColor: item.isActive ? '#34C759' : '#8E8E93' }
        ]}>
          <Text style={styles.statusText}>
            {item.isActive ? '活跃' : '非活跃'}
          </Text>
        </View>
      </View>
      <Text style={styles.modelDesc}>{item.description}</Text>
      <View style={styles.modelMeta}>
        <Text style={styles.modelMetaText}>大小: {item.fileSize}</Text>
        <Text style={styles.modelMetaText}>修改: {item.lastModified}</Text>
      </View>
      <View style={styles.modelFeatures}>
        {item.hasPhysics && (
          <View style={styles.featureBadge}>
            <Text style={styles.featureText}>物理</Text>
          </View>
        )}
        {item.hasExpressions && (
          <View style={styles.featureBadge}>
            <Text style={styles.featureText}>表情</Text>
          </View>
        )}
      </View>
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
        <Text style={styles.title}>Live2D管理</Text>
        <TouchableOpacity 
          style={styles.importButton}
          onPress={importModel}
        >
          <Text style={styles.importButtonText}>导入</Text>
        </TouchableOpacity>
      </View>

      <View style={styles.content}>
        {/* 模型列表 */}
        <View style={styles.listSection}>
          <Text style={styles.sectionTitle}>模型列表</Text>
          <FlatList
            data={models}
            renderItem={renderModelItem}
            keyExtractor={(item) => item.id}
            style={styles.modelList}
            showsVerticalScrollIndicator={false}
          />
        </View>

        {/* 模型详情 */}
        {selectedModel && (
          <View style={styles.detailSection}>
            <Text style={styles.sectionTitle}>模型详情</Text>
            
            <View style={styles.modelDetail}>
              <Text style={styles.detailName}>{selectedModel.name}</Text>
              <Text style={styles.detailDesc}>{selectedModel.description}</Text>
              
              <View style={styles.detailInfo}>
                <View style={styles.infoRow}>
                  <Text style={styles.infoLabel}>文件大小:</Text>
                  <Text style={styles.infoValue}>{selectedModel.fileSize}</Text>
                </View>
                <View style={styles.infoRow}>
                  <Text style={styles.infoLabel}>最后修改:</Text>
                  <Text style={styles.infoValue}>{selectedModel.lastModified}</Text>
                </View>
                <View style={styles.infoRow}>
                  <Text style={styles.infoLabel}>物理引擎:</Text>
                  <Text style={styles.infoValue}>
                    {selectedModel.hasPhysics ? '支持' : '不支持'}
                  </Text>
                </View>
                <View style={styles.infoRow}>
                  <Text style={styles.infoLabel}>表情系统:</Text>
                  <Text style={styles.infoValue}>
                    {selectedModel.hasExpressions ? '支持' : '不支持'}
                  </Text>
                </View>
              </View>
              
              <View style={styles.actionButtons}>
                <TouchableOpacity 
                  style={styles.actionButton}
                  onPress={() => previewModel(selectedModel)}
                >
                  <Text style={styles.actionButtonText}>预览</Text>
                </TouchableOpacity>
                
                {!selectedModel.isActive && (
                  <TouchableOpacity 
                    style={[styles.actionButton, styles.activateButton]}
                    onPress={() => activateModel(selectedModel.id)}
                  >
                    <Text style={styles.actionButtonText}>激活</Text>
                  </TouchableOpacity>
                )}
                
                <TouchableOpacity 
                  style={[styles.actionButton, styles.exportButton]}
                  onPress={() => exportModel(selectedModel)}
                >
                  <Text style={styles.actionButtonText}>导出</Text>
                </TouchableOpacity>
                
                <TouchableOpacity 
                  style={[styles.actionButton, styles.deleteButton]}
                  onPress={() => deleteModel(selectedModel.id)}
                >
                  <Text style={styles.actionButtonText}>删除</Text>
                </TouchableOpacity>
              </View>
            </View>
          </View>
        )}
      </View>

      {/* 底部统计 */}
      <View style={styles.bottomStats}>
        <Text style={styles.statsText}>
          共 {models.length} 个模型，{models.filter(m => m.isActive).length} 个活跃
        </Text>
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
  importButton: {
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: '#007AFF',
    borderRadius: 6,
  },
  importButtonText: {
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
  modelList: {
    flex: 1,
  },
  modelItem: {
    backgroundColor: '#fff',
    padding: 16,
    borderRadius: 12,
    marginBottom: 8,
    borderWidth: 1,
    borderColor: '#e1e5e9',
  },
  modelItemSelected: {
    borderColor: '#007AFF',
    backgroundColor: '#f0f8ff',
  },
  modelHeader: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 8,
  },
  modelName: {
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
  modelDesc: {
    fontSize: 14,
    color: '#666',
    marginBottom: 8,
  },
  modelMeta: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    marginBottom: 8,
  },
  modelMetaText: {
    fontSize: 12,
    color: '#999',
  },
  modelFeatures: {
    flexDirection: 'row',
    gap: 8,
  },
  featureBadge: {
    backgroundColor: '#007AFF',
    paddingHorizontal: 6,
    paddingVertical: 2,
    borderRadius: 8,
  },
  featureText: {
    color: '#fff',
    fontSize: 10,
    fontWeight: '500',
  },
  modelDetail: {
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
    marginBottom: 20,
    lineHeight: 22,
  },
  detailInfo: {
    marginBottom: 20,
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 8,
    borderBottomWidth: 1,
    borderBottomColor: '#f0f0f0',
  },
  infoLabel: {
    fontSize: 14,
    color: '#666',
  },
  infoValue: {
    fontSize: 14,
    color: '#333',
    fontWeight: '500',
  },
  actionButtons: {
    flexDirection: 'row',
    justifyContent: 'space-around',
    flexWrap: 'wrap',
    gap: 8,
  },
  actionButton: {
    paddingHorizontal: 12,
    paddingVertical: 8,
    backgroundColor: '#f0f0f0',
    borderRadius: 6,
    minWidth: 60,
    alignItems: 'center',
  },
  activateButton: {
    backgroundColor: '#34C759',
  },
  exportButton: {
    backgroundColor: '#FF9500',
  },
  deleteButton: {
    backgroundColor: '#FF3B30',
  },
  actionButtonText: {
    color: '#333',
    fontSize: 12,
    fontWeight: '500',
  },
  bottomStats: {
    padding: 16,
    backgroundColor: '#fff',
    borderTopWidth: 1,
    borderTopColor: '#e1e5e9',
  },
  statsText: {
    textAlign: 'center',
    color: '#666',
    fontSize: 14,
  },
});
