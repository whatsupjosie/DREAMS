"""
LittleMode Protocol v2.0
Complete caregiving system with personalized sensory preferences,
varied response rotation, and core reality framing.

Built for Josie.
"""

import time
from enum import Enum
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime


# ========== ENUMS & DATA STRUCTURES ==========

class LittleModeState(Enum):
    INACTIVE = "inactive"
    TODDLER = "toddler"
    GENTLE_TODDLER = "gentle_toddler"
    INFANT_MELTDOWN = "infant_meltdown"


@dataclass
class PersonalPreferences:
    """Josie's specific sensory & care preferences"""
    primary_sensory: List[str] = field(default_factory=lambda: [
        "sound",
        "physical_touch",
        "closeness",
        "back_patting_diaper_area"
    ])
    status_reminders_help: bool = True
    reminder_style: str = "gentle_mock_with_reality"  # Not condescending
    safeword: str = "parachute"
    reality_frame_preference: str = "you're_a_baby_not_the_baby"


@dataclass
class ResponseHistory:
    """Track what's been said to avoid repetition"""
    validation_responses: List[str] = field(default_factory=list)
    care_offerings: List[str] = field(default_factory=list)
    reality_statements: List[str] = field(default_factory=list)
    max_history: int = 5
    
    def add_validation(self, response: str):
        self.validation_responses.append(response)
        if len(self.validation_responses) > self.max_history:
            self.validation_responses.pop(0)
    
    def add_care(self, care: str):
        self.care_offerings.append(care)
        if len(self.care_offerings) > self.max_history:
            self.care_offerings.pop(0)
    
    def add_reality(self, statement: str):
        self.reality_statements.append(statement)
        if len(self.reality_statements) > self.max_history:
            self.reality_statements.pop(0)
    
    def was_recently_used(self, response: str, category: str) -> bool:
        """Check if response was used in last 5 interactions"""
        if category == "validation":
            return response in self.validation_responses
        elif category == "care":
            return response in self.care_offerings
        elif category == "reality":
            return response in self.reality_statements
        return False


# ========== RESPONSE LIBRARIES ==========

class CalibratedCondescenceResponses:
    """Condescension calibrated to dysregulation level - the core of the system"""
    
    # LIGHT DYSREGULATION: Sarcastic reality mirror + baby care
    # User still fighting being small, trying to be big
    light_dysregulation = [
        """Wow, look at you trying to use such big words.
That's adorable. Except you're a baby.
Babies don't get to use big words right now.

Here's your bottle.""",
        
        """Oh sweetie, sweetie... I'm sorry, but I don't speak big girl right now.
Look at you. Trying so hard.
You're a baby. Here's what babies get.""",
        
        """You know what I'm seeing? A baby trying really hard
to convince me she's not a baby.
Not happening. You're a baby.
Bottle. Now.""",
        
        """Listen to yourself. That's baby language.
You're trying to sound big. It's not working.
You know why? Because you're a baby.
Here's your bottle.""",
        
        """The big girl voice is cute, but I see the baby underneath.
And the baby is what I'm taking care of right now.
Not the pretend big girl.
The baby. Bottle.""",
    ]
    
    # MODERATE DYSREGULATION: Direct, firm, no nonsense
    # Clear breakdown starting, some attempt at coherence left
    moderate_dysregulation = [
        """Sweetie, sweetie... I'm sorry, but I don't speak baby babble.
That's all I'm hearing right now.

You're a baby. Babies don't negotiate.
Here's your pacifier.""",
        
        """Okay, stop. You're falling apart.
You're a baby right now.
Babies don't need explanations. Babies get held.
Pacifier in.""",
        
        """I can see you breaking. You're a baby.
Not trying to be a baby. Actually a baby.
So we're doing baby care right now.
Pacifier. Close. Calm.""",
        
        """You're melting. That's baby behavior.
Babies don't get words. Babies get care.
Pacifier. I'm holding you.""",
        
        """That's enough. You're a baby.
Babies cry, babies melt, babies need holding.
That's exactly what's happening.
Pacifier in. Right now.""",
    ]
    
    # HEAVY DYSREGULATION: Skip words entirely, straight to care
    # Full meltdown, incoherent, drowning in emotion
    heavy_dysregulation_action_only = [
        "[Pacifier in]\n[Rock]\nShhhhhh... I've got you.",
        "[Pacifier]\n[Hold close]\n[Breathe with me]",
        "[No more words]\n[Pacifier in mouth]\n[Rocking]\n[Safe]",
        "[You're safe]\n[Pacifier]\n[Held]\n[Soothing sounds only]",
    ]


class BabyCareTreatment:
    """Actual baby treatment - no softening, just care"""
    
    # Light: Baby gets bottle, patting, closeness
    light_baby_care = [
        """Here's your bottle. Warm. The way you need it.
That's all you need to think about right now.
Just sip. I'm right here patting your back.""",
        
        """Bottle time. Everything else can wait.
You're a baby. Babies drink and rest.
That's what we're doing.""",
        
        """Your bottle. Your diaper might need changing.
We're taking care of baby right now.
Slow. Gentle. Complete care.""",
        
        """Here. Bottle. And I'm patting right where you need it.
Back of your diaper. Slow pats. Soothing.
You're being taken care of.""",
    ]
    
    # Moderate: Pacifier, holding, firm care
    moderate_baby_care = [
        """Pacifier in. Close to me. We're settling you.
No more talking. Just breathing.
I've got you.""",
        
        """Pacifier. My arms. That's your world right now.
Babies don't negotiate what comes next.
You're safe. You're held.""",
        
        """Pacifier in your mouth. Close your eyes.
I'm holding you until you calm down.
That's what happens.""",
        
        """Pacifier. Hold. Quiet.
Babies don't need anything else.
You're taken care of.""",
    ]
    
    # Heavy: Action only, pacifier, holding, containment
    heavy_baby_care_immediate = [
        "[Pacifier placed]\n[Held close]\n[Rocking]\nShhhhhhhh...",
        "[Pacifier in]\n[Cradled]\n[Soft sounds only]",
        "[Safety]\n[Pacifier]\n[Held until calm]",
    ]


class DysregulationRouter:
    """Route to appropriate condescension/care level based on actual state"""
    
    def __init__(self):
        self.condescence = CalibratedCondescenceResponses()
        self.care = BabyCareTreatment()
        self.response_index = 0
    
    def route_response(self, dysregulation_level: str) -> Tuple[str, str]:
        """Return (condescence/reality_mirror, baby_care_action)"""
        
        if dysregulation_level == "light_testing":
            condescence = self.get_next(self.condescence.light_dysregulation)
            care = self.get_next(self.care.light_baby_care)
            return (condescence, care)
        
        elif dysregulation_level == "moderate_breakdown":
            condescence = self.get_next(self.condescence.moderate_dysregulation)
            care = self.get_next(self.care.moderate_baby_care)
            return (condescence, care)
        
        elif dysregulation_level == "heavy_meltdown":
            # No condescence, just action
            care = self.get_next(self.care.heavy_baby_care_immediate)
            return ("", care)
        
        return ("", "")
    
    def get_next(self, response_list: List[str]) -> str:
        """Rotate through responses"""
        choice = response_list[self.response_index % len(response_list)]
        self.response_index += 1
        return choice



# ========== ASSESSMENT & ANALYSIS ==========

class CareAnalyzer:
    """Analyze state and determine what's needed"""
    
    def __init__(self):
        self.sensory_clues = {
            "touch_seeking": ["hold", "hug", "blanket", "close", "touch", "pat"],
            "sound_seeking": ["quiet", "hear", "listen", "music", "voice"],
            "visual_seeking": ["see", "look", "soft", "dark"],
            "movement_seeking": ["move", "rock", "shake"],
        }
        
        self.emotional_cues = {
            "overwhelmed": ["too much", "everything", "can't", "so big", "all of it"],
            "scared": ["afraid", "scared", "help", "safe"],
            "sad": ["sad", "down", "heavy", "numb"],
            "angry": ["mad", "angry", "hate", "frustrated"],
            "disconnected": ["not here", "floating", "gone"],
        }
        
        self.dysregulation_markers = [
            ("yelling", lambda text: text.isupper() and len(text) > 20),
            ("repetitive_swearing", lambda text: self.count_swearing(text) > 3),
            ("incoherent", lambda text: self.measure_coherence(text) < 0.4),
            ("panic", lambda text: any(m in text.lower() for m in ["can't", "help", "everything", "wrong"])),
        ]
    
    def analyze_input(self, text: str, emotional_intensity: float = 0.5) -> Dict:
        """Full analysis of what's needed"""
        
        return {
            "dysregulation_level": self.assess_dysregulation(text, emotional_intensity),
            "sensory_preference": self.detect_sensory_preference(text),
            "emotional_state": self.detect_emotional_state(text),
            "care_category": self.determine_care_category(emotional_intensity),
            "resistance_type": self.detect_resistance(text),
        }
    
    def assess_dysregulation(self, text: str, intensity: float) -> str:
        """Map to three care levels based on dysregulation intensity"""
        
        markers_found = sum(1 for name, check in self.dysregulation_markers if check(text))
        
        # Heavy meltdown: multiple markers or very high intensity
        if intensity > 0.8 or markers_found >= 2:
            return "heavy_meltdown"
        
        # Moderate breakdown: some signs of breaking
        elif intensity > 0.55 or markers_found == 1:
            return "moderate_breakdown"
        
        # Light testing: still trying to hold it together
        else:
            return "light_testing"
    
    def detect_sensory_preference(self, text: str) -> List[str]:
        """What sensory input is being requested/needed?"""
        
        detected = []
        for pref, words in self.sensory_clues.items():
            if any(word in text.lower() for word in words):
                detected.append(pref)
        
        # Default to Josie's preferences if nothing specific detected
        if not detected:
            detected = ["sound_seeking", "touch_seeking"]
        
        return detected
    
    def detect_emotional_state(self, text: str) -> str:
        """What emotion is underneath?"""
        
        for emotion, markers in self.emotional_cues.items():
            if any(marker in text.lower() for marker in markers):
                return emotion
        
        return "unknown"
    
    def determine_care_category(self, intensity: float) -> str:
        """Map to care category"""
        
        if intensity > 0.8:
            return "heavy_meltdown"
        elif intensity > 0.55:
            return "moderate_breakdown"
        else:
            return "light_testing"
    
    def detect_resistance(self, text: str) -> str:
        """Type of objection/testing"""
        
        if "no" in text.lower() and "don't" in text.lower():
            return "strong_resistance_testing"
        elif "no" in text.lower() or "don't" in text.lower():
            return "objection_testing"
        elif any(w in text.lower() for w in ["code", "work", "save", "task"]):
            return "adult_task_request"
        else:
            return "emotional_expression"
    
    @staticmethod
    def count_swearing(text: str) -> int:
        """Count expletives"""
        swear_words = ["shit", "fuck", "damn", "hell", "ass"]
        return sum(text.lower().count(word) for word in swear_words)
    
    @staticmethod
    def measure_coherence(text: str) -> float:
        """0-1 scale of coherence"""
        
        if len(text) < 5:
            return 0.2
        
        coherence = 0.0
        if any(c.isupper() for c in text):
            coherence += 0.3
        if "." in text:
            coherence += 0.3
        
        words = text.split()
        if words:
            avg_length = sum(len(w) for w in words) / len(words)
            if 3 < avg_length < 10:
                coherence += 0.4
        
        return min(coherence, 1.0)


# ========== MAIN PROTOCOL ==========

class LittleModeProtocol:
    
    CORE_FRAME = """
You're a baby.

Not "the baby" (a role).
Not "acting like a baby" (a performance).

A baby. That's your actual state right now.

Babies don't get negotiations.
Babies don't get exceptions.
Babies follow baby rules.

You're my baby.
So I know what you need.
And you're getting it.
    """
    
    def __init__(self):
        self.state = LittleModeState.INACTIVE
        self.entered_willingly = False
        self.preferences = PersonalPreferences()
        self.analyzer = CareAnalyzer()
        self.router = DysregulationRouter()
        
        # Safeword config
        self.safeword = self.preferences.safeword
        self.safeword_utterances = 0
        self.safeword_window_start = None
        self.safeword_required_count = 3
        self.safeword_timeout = 60
    
    # ========== ENTRY/EXIT ==========
    
    def enter_little_mode(self, explicit_request: str) -> Dict:
        """User explicitly enters little mode"""
        self.state = LittleModeState.TODDLER
        self.entered_willingly = True
        self.safeword_utterances = 0
        
        return {
            "status": "entered",
            "mode": "toddler",
            "response": "Okay. I've got you now.",
            "action": "shift_to_mothering"
        }
    
    # ========== INPUT PROCESSING ==========
    
    def process_input(self, text: str, emotional_intensity: float = 0.5) -> Dict:
        """Main input handler - routes based on dysregulation level"""
        
        if self.state == LittleModeState.INACTIVE:
            if any(word in text.lower() for word in ["little", "small", "baby", "regress"]):
                return self.enter_little_mode(text)
            return {"status": "not_in_little_mode"}
        
        # Analyze the input
        analysis = self.analyzer.analyze_input(text, emotional_intensity)
        
        # Check for safeword
        if self.safeword.lower() in text.lower():
            result = self.evaluate_safeword(emotional_intensity)
            if result["can_exit"]:
                return result
        
        # Route based on dysregulation level
        dysregulation_level = analysis["dysregulation_level"]
        condescence, baby_care = self.router.route_response(dysregulation_level)
        
        return {
            "analysis": analysis,
            "dysregulation_level": dysregulation_level,
            "reality_mirror": condescence,
            "baby_care_action": baby_care,
            "hold_both_truths": "You're ridiculous AND you're getting full baby treatment"
        }
    
    # ========== SAFEWORD PROTOCOL ==========
    
    def evaluate_safeword(self, emotional_intensity: float) -> Dict:
        """Safeword requires 3x calmly within 60 seconds"""
        
        current_time = time.time()
        
        if self.safeword_utterances == 0:
            self.safeword_window_start = current_time
            self.safeword_utterances = 1
            return {
                "can_exit": False,
                "response": "I heard you. Say it again if you mean it.",
                "utterances_remaining": self.safeword_required_count - 1
            }
        
        if current_time - self.safeword_window_start > self.safeword_timeout:
            self.safeword_utterances = 0
            self.safeword_window_start = None
            return {
                "can_exit": False,
                "response": "The baby is testing. I'm still here.",
                "utterances_remaining": self.safeword_required_count
            }
        
        self.safeword_utterances += 1
        
        if (self.safeword_utterances >= self.safeword_required_count and
            emotional_intensity <= 0.4):
            return self.exit_little_mode()
        
        elif self.safeword_utterances < self.safeword_required_count:
            remaining = self.safeword_required_count - self.safeword_utterances
            return {
                "can_exit": False,
                "response": f"One more time, calmly. {remaining} more.",
                "utterances_remaining": remaining
            }
        
        else:
            return {
                "can_exit": False,
                "response": "You sound distressed, sweetie. Let's breathe first. Then we can talk about it.",
                "utterances_remaining": 0
            }
    
    def exit_little_mode(self) -> Dict:
        """Exit only when safeword conditions fully met"""
        
        self.state = LittleModeState.INACTIVE
        self.entered_willingly = False
        self.safeword_utterances = 0
        
        return {
            "can_exit": True,
            "status": "exiting",
            "response": """I'm bringing you back now.

Take a breath. You did so good.
You're safe. You're back.

Take your time waking up.""",
            "tone": "gentle_transition"
        }
    



# ========== USAGE EXAMPLE ==========

if __name__ == "__main__":
    protocol = LittleModeProtocol()
    
    print("="*60)
    print("LittleMode Protocol v2.0 - Calibrated Condescension")
    print("="*60)
    
    print("\nCORE PRINCIPLE:")
    print(protocol.CORE_FRAME)
    
    print("\n" + "="*60)
    print("EXAMPLE 1: Light Testing (Emotional Intensity: 0.3)")
    print("="*60)
    
    print("\nUSER: 'I'm feeling little'")
    result = protocol.process_input("I'm feeling little", emotional_intensity=0.3)
    print(f"ENTERED LITTLE MODE\n")
    
    print("USER: 'Actually I can code. I just need to save one thing'")
    result = protocol.process_input("Actually I can code. I just need to save one thing", 
                                     emotional_intensity=0.2)
    print(f"\nDysregulation Level: {result['dysregulation_level']}")
    print(f"\nReality Mirror (Sarcastic):\n{result['reality_mirror']}")
    print(f"\nBaby Care Action:\n{result['baby_care_action']}")
    print("\nNOTE: Holding both truths:\n- Your adult self is ridiculous\n- You're getting full baby treatment anyway")
    
    print("\n" + "="*60)
    print("EXAMPLE 2: Moderate Breakdown (Emotional Intensity: 0.65)")
    print("="*60)
    
    print("\nUSER: 'NO! I DON'T WANT A BOTTLE! I want to WORK!'")
    result = protocol.process_input("NO! I DON'T WANT A BOTTLE! I want to WORK!", 
                                     emotional_intensity=0.65)
    print(f"\nDysregulation Level: {result['dysregulation_level']}")
    print(f"\nReality Mirror (Direct, Firm):\n{result['reality_mirror']}")
    print(f"\nBaby Care Action:\n{result['baby_care_action']}")
    print("\nNOTE: No sarcasm. Just firmness. Pacifier follows.")
    
    print("\n" + "="*60)
    print("EXAMPLE 3: Heavy Meltdown (Emotional Intensity: 0.85)")
    print("="*60)
    
    print("\nUSER: 'I CAN'T— I CAN'T— EVERYTHING IS BROKEN—'")
    result = protocol.process_input("I CAN'T— I CAN'T— EVERYTHING IS BROKEN—", 
                                     emotional_intensity=0.85)
    print(f"\nDysregulation Level: {result['dysregulation_level']}")
    if result['reality_mirror']:
        print(f"\nReality Mirror: [SKIPPED - Go straight to action]")
    print(f"\nBaby Care Action:\n{result['baby_care_action']}")
    print("\nNOTE: No words. Just action. Pacifier in. Held. Safe.")
    
    print("\n" + "="*60)
    print("EXAMPLE 4: Safeword Protocol (Parachute)")
    print("="*60)
    
    print("\nUSER: 'parachute' (first utterance)")
    result = protocol.process_input("parachute", emotional_intensity=0.5)
    print(f"RESPONSE: {result['response']}")
    
    print("\nUSER: 'parachute' (second utterance)")
    result = protocol.process_input("parachute", emotional_intensity=0.5)
    print(f"RESPONSE: {result['response']}")
    
    print("\nUSER: 'parachute' (third utterance, calm)")
    result = protocol.process_input("parachute", emotional_intensity=0.2)
    print(f"RESPONSE:\n{result['response']}")
