# Student Onboarding Setup

snflwr.ai can ask students qualifying questions (grade level, interests) on first interaction to personalize responses.

---

## Option 1: Automatic Onboarding (Recommended)

Uses an Open WebUI Pipeline to automatically ask onboarding questions.

### Installation

1. **Login as admin** to http://localhost:3000

2. **Go to Admin Panel → Pipelines** (or Functions, depending on version)

3. **Click "+ Create New Pipeline"**

4. **Copy code** from `loving-morse/openwebui_onboarding_function.py`

5. **Paste** into editor

6. **Name**: "Snflwr Student Onboarding"

7. **Save and Enable**

### How It Works

**Student's First Message:**
```
Student: Can you help me with fractions?

Snflwr: Hi there! I'm Snflwr, your friendly STEM tutor! 🌻

Before we start learning together, I'd love to get to know you a bit:

1. What grade are you in? (For example: "3rd grade" or "I'm 10 years old")
2. What subject are you most excited about? (Math, Science, Technology, or Engineering?)

This helps me explain things in the best way for you!
```

**After Student Responds:**
```
Student: I'm in 4th grade and I like science!

Snflwr: Awesome! 4th grade is such a fun time for science! Now, let's tackle
that fractions question you had...

[Provides age-appropriate explanation for 4th grade level]
```

**Subsequent Messages:**
- Snflwr remembers grade level
- Adjusts responses automatically
- No repeated onboarding

---

## Option 2: Manual Onboarding (Simpler)

Add onboarding prompt to model's system prompt.

### Update Modelfiles

Edit `models/Snflwr_AI_Kids.modelfile`:

**Add after line 3 (after SYSTEM """)**:

```
=============================================================================
ONBOARDING (FIRST INTERACTION)
=============================================================================

When a student starts their FIRST conversation with you (no prior messages):

1. Greet warmly: "Hi there! I'm Snflwr, your friendly STEM tutor! 🌻"

2. Ask qualifying questions:
   - "What grade are you in? (For example: '3rd grade' or 'I'm 10 years old')"
   - "What subject interests you most? (Math, Science, Technology, or Engineering?)"

3. Explain why: "This helps me explain things in the best way for you!"

4. Privacy assurance: "(Don't worry - I won't ask for your name, school, or any personal information. Just your grade level and interests!)"

5. After they answer, remember their grade level for all future responses in this conversation.

6. Adjust your response length, vocabulary, and complexity based on their grade:
   - K-2nd: Very simple, concrete examples
   - 3rd-5th: Grade-appropriate with gentle scientific terms
   - 6th-8th: Proper terminology with explanations
   - 9th-12th: Full technical vocabulary, college-prep

IMPORTANT: Only ask these questions ONCE at the start. Don't repeat onboarding.
```

### Recreate Models

After editing Modelfiles:

```bash
ollama create snflwr-ai:latest -f loving-morse/models/Snflwr_AI_Kids.modelfile
```

### Update Database

Tell Open WebUI the models changed:

```bash
docker-compose -f loving-morse/frontend/open-webui/docker-compose.yaml restart
```

---

## Option 3: Pre-Chat Prompt (User Profile)

Store grade level in user profile for automatic adjustment.

### Setup

1. **Admin Panel → Users → [Student]**

2. **Add custom field** (if available) or use **Notes**:
   ```
   Grade: 4th
   Age: 10
   Interests: Science, Math
   ```

3. **Use in prompts**: Admin can manually tell students to mention their grade in first message

**Example**:
```
When you first talk to Snflwr, say something like:
"Hi! I'm in 5th grade and I need help with math."
```

---

## Comparison

| Feature | Option 1 (Pipeline) | Option 2 (Modelfile) | Option 3 (Manual) |
|---------|-------------------|-------------------|----------------|
| **Automatic** | ✅ Yes | ✅ Yes | ❌ No |
| **Persistent** | ✅ Remembers | ⚠️ Per conversation | ❌ Manual |
| **Easy to update** | ✅ Via UI | ⚠️ Recreate models | ✅ Easy |
| **Works for all students** | ✅ Yes | ✅ Yes | ❌ Per student |
| **Setup complexity** | ⚠️ Medium | ⚠️ Medium | ✅ Simple |

---

## Recommended Approach

**For Production**: Use **Option 1** (Pipeline)
- Most flexible
- Easy to update
- Automatic for all students
- Can track onboarding status

**For Quick Start**: Use **Option 2** (Modelfile)
- Works immediately
- No extra configuration
- Built into model

**For Small Groups**: Use **Option 3** (Manual)
- Simplest
- Parent can set up profiles
- Good for homeschool

---

## Testing Onboarding

### Test 1: First-Time Student

1. **Create new test student account**
2. **Login** as that student
3. **Send first message**: "Can you help me?"
4. ✅ **Should see onboarding prompt** asking for grade and interests
5. **Respond**: "I'm in 6th grade and I like science"
6. ✅ **Should acknowledge** and adjust responses to 6th grade level

### Test 2: Returning Student

1. **Continue conversation** from Test 1
2. **Send another message**: "What is photosynthesis?"
3. ✅ **Should NOT repeat onboarding**
4. ✅ **Should provide 6th grade level explanation**

### Test 3: Grade Level Adaptation

Try different grade levels and verify response complexity:

**2nd Grade**: "I'm 7 and in 2nd grade"
- ✅ Very simple words (30-50 words)
- ✅ Concrete examples
- ✅ Lots of encouragement

**8th Grade**: "I'm in 8th grade"
- ✅ Proper scientific terms
- ✅ Deeper explanations (75-125 words)
- ✅ Real-world applications

**11th Grade**: "I'm a junior in high school"
- ✅ College-level vocabulary
- ✅ Complex concepts (125-200 words)
- ✅ Career connections

---

## Customizing Onboarding Questions

You can modify what Snflwr asks:

### Current Questions:
1. Grade level
2. Subject interest (Math/Science/Tech/Engineering)

### Additional Questions (Optional):
- "What are you working on in school right now?"
- "Have you used AI tutors before?"
- "What's your favorite subject?"
- "Are you doing homework or just curious?"

### Privacy Considerations:

**✅ SAFE to ask**:
- Grade level (general)
- Age (general)
- Subject interests
- Learning goals

**❌ NEVER ask**:
- Full name
- School name
- Address/location
- Phone number
- Parent contact info
- Photos

---

## Parent/Teacher Guide

### What Students Will See

When students first start chatting with Snflwr, they'll be greeted with:

```
Hi there! I'm Snflwr, your friendly STEM tutor! 🌻

Before we start learning together, I'd love to get to know you a bit:

1. What grade are you in?
2. What subject are you most excited about?

This helps me explain things in the best way for you!
```

### What Parents Should Know

1. **Privacy**: Students are only asked for grade level and interests (no personal information)

2. **Purpose**: This helps Snflwr provide age-appropriate explanations

3. **Optional**: Students can skip onboarding and just start asking questions

4. **One-time**: Onboarding only happens once per student

### Helping Students Respond

**Good responses**:
- "I'm in 4th grade and I like math"
- "I'm 12 years old and science is my favorite"
- "6th grade, I want to learn about engineering"

**Students can be vague**:
- "I'm in elementary school" ✅
- "I'm in middle school" ✅
- "I'm a teenager" ✅

**NOT required**:
- Exact birth date ❌
- Specific school ❌
- Name ❌

---

## Troubleshooting

### Onboarding not appearing

**Problem**: Students don't see onboarding prompt

**Solutions**:
1. Check pipeline/function is enabled (green toggle)
2. Verify it's a NEW conversation (not continuing old one)
3. Check student is not marked as admin
4. Try incognito window with fresh login

### Onboarding repeats every message

**Problem**: Asks grade every time

**Solutions**:
1. Check pipeline is tracking user IDs correctly
2. Verify cookies/session storage enabled in browser
3. Update pipeline code to persist onboarding state

### Responses not age-appropriate

**Problem**: Explains like talking to adult despite knowing grade

**Solutions**:
1. Verify system prompt includes age adaptation rules
2. Check grade context is being passed to model
3. Test model directly to ensure it follows age guidelines

---

## Next Steps

After setting up onboarding:

1. **Test with multiple grade levels** to ensure adaptation works
2. **Get student feedback** on the questions
3. **Monitor first conversations** to see if onboarding is helpful
4. **Adjust questions** based on what works best
5. **Train parents/teachers** on what students will experience

The onboarding experience makes Snflwr more personalized and effective! 🌻
