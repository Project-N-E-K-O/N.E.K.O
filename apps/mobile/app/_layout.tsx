import { Stack } from 'expo-router';
import { StatusBar } from 'expo-status-bar';

export default function RootLayout() {
  return (
    <>
      <StatusBar style="auto" />
      <Stack>
        <Stack.Screen 
          name="index" 
          options={{ 
            title: 'Xiao8',
            headerShown: false 
          }} 
        />
        <Stack.Screen 
          name="viewer" 
          options={{ 
            title: 'Live2D Viewer',
            headerShown: false 
          }} 
        />
        <Stack.Screen 
          name="memory-browser" 
          options={{ 
            title: 'Memory Browser',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="subtitle" 
          options={{ 
            title: 'Subtitle Manager',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="voice-clone" 
          options={{ 
            title: 'Voice Clone',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="api-key-settings" 
          options={{ 
            title: 'API Settings',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="chara-manager" 
          options={{ 
            title: 'Character Manager',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="l2d-manager" 
          options={{ 
            title: 'Live2D Manager',
            presentation: 'modal'
          }} 
        />
        <Stack.Screen 
          name="live2d-emotion-manager" 
          options={{ 
            title: 'Emotion Manager',
            presentation: 'modal'
          }} 
        />
      </Stack>
    </>
  );
}
