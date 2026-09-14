class EmotionEngine:
    """
    Drives the embodied emotional engine for N.E.K.O.
    Processes user interaction sentiment to update the companion's mood state.
    """
    def __init__(self):
        self.mood = "neutral"
        self.affection = 50 # 0-100

    def process_sentiment(self, sentiment_score):
        if sentiment_score > 0.5:
            self.mood = "happy"
            self.affection = min(self.affection + 5, 100)
        elif sentiment_score < -0.5:
            self.mood = "sad"
            self.affection = max(self.affection - 5, 0)
        return self.mood

    def get_personality_modifiers(self):
        # Returns prompt modifiers based on current emotion
        if self.mood == "happy":
            return "Be cheerful and use emojis like ✨ and 🐾."
        return "Be supportive and attentive."
