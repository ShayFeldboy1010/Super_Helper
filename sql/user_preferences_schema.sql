-- User Preferences & Learning System
-- Run this in Supabase SQL Editor

-- 1. User Preferences Table (explicit + learned preferences)
CREATE TABLE IF NOT EXISTS user_preferences (
    user_id BIGINT PRIMARY KEY,

    -- Explicit preferences (user-set)
    language TEXT DEFAULT 'he' CHECK (language IN ('he', 'en')),
    response_style TEXT DEFAULT 'concise' CHECK (response_style IN ('concise', 'detailed')),
    quiet_hours_start INT DEFAULT 22 CHECK (quiet_hours_start >= 0 AND quiet_hours_start <= 23),
    quiet_hours_end INT DEFAULT 7 CHECK (quiet_hours_end >= 0 AND quiet_hours_end <= 23),
    stock_alerts_enabled BOOLEAN DEFAULT true,
    daily_brief_enabled BOOLEAN DEFAULT true,

    -- Learned preferences (auto-detected)
    peak_hour INT,                          -- Most active hour (0-23)
    preferred_day INT,                      -- Most active day (0=Sun, 6=Sat)
    interests JSONB DEFAULT '[]'::jsonb,    -- ["stocks", "AI", "productivity"]
    morning_person BOOLEAN,                 -- true if peak_hour < 10

    -- Metadata
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- 2. Add analytics columns to interaction_log
ALTER TABLE interaction_log
ADD COLUMN IF NOT EXISTS response_length INT,
ADD COLUMN IF NOT EXISTS user_satisfaction TEXT CHECK (user_satisfaction IN ('positive', 'neutral', 'negative')),
ADD COLUMN IF NOT EXISTS had_followup BOOLEAN DEFAULT false;

-- 3. Create index for faster pattern queries
CREATE INDEX IF NOT EXISTS idx_interaction_log_user_created
ON interaction_log (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interaction_log_action_type
ON interaction_log (user_id, action_type);

-- 4. User Patterns View (computed from interaction_log)
CREATE OR REPLACE VIEW user_patterns AS
SELECT
    user_id,
    COUNT(*) as total_interactions,

    -- Time patterns
    MODE() WITHIN GROUP (ORDER BY EXTRACT(HOUR FROM created_at)::INT) as peak_hour,
    MODE() WITHIN GROUP (ORDER BY EXTRACT(DOW FROM created_at)::INT) as preferred_day,

    -- Action distribution
    COUNT(*) FILTER (WHERE action_type = 'query') as query_count,
    COUNT(*) FILTER (WHERE action_type = 'task') as task_count,
    COUNT(*) FILTER (WHERE action_type = 'calendar') as calendar_count,
    COUNT(*) FILTER (WHERE action_type = 'chat') as chat_count,
    COUNT(*) FILTER (WHERE action_type = 'note') as note_count,

    -- Satisfaction metrics
    COUNT(*) FILTER (WHERE user_satisfaction = 'positive') as positive_count,
    COUNT(*) FILTER (WHERE user_satisfaction = 'negative') as negative_count,
    COUNT(*) FILTER (WHERE had_followup = true) as followup_count,

    -- Averages
    AVG(response_length) as avg_response_length,
    AVG(EXTRACT(HOUR FROM created_at)) as avg_hour

FROM interaction_log
WHERE created_at > NOW() - INTERVAL '30 days'
GROUP BY user_id;

-- 5. Topic frequency tracking (for interest inference)
CREATE OR REPLACE VIEW user_topic_frequency AS
SELECT
    user_id,
    -- Stock-related queries
    COUNT(*) FILTER (WHERE
        user_message ILIKE '%מניות%' OR
        user_message ILIKE '%stock%' OR
        user_message ILIKE '%nvda%' OR
        user_message ILIKE '%tsla%' OR
        user_message ILIKE '%שוק%'
    ) as stock_queries,

    -- AI-related queries
    COUNT(*) FILTER (WHERE
        user_message ILIKE '%ai%' OR
        user_message ILIKE '%בינה מלאכותית%' OR
        user_message ILIKE '%llm%' OR
        user_message ILIKE '%gpt%' OR
        user_message ILIKE '%claude%'
    ) as ai_queries,

    -- Calendar/productivity
    COUNT(*) FILTER (WHERE
        user_message ILIKE '%יומן%' OR
        user_message ILIKE '%פגישה%' OR
        user_message ILIKE '%תזכיר%' OR
        user_message ILIKE '%calendar%'
    ) as productivity_queries,

    -- Total for percentage calculation
    COUNT(*) as total_queries

FROM interaction_log
WHERE created_at > NOW() - INTERVAL '30 days'
AND action_type = 'query'
GROUP BY user_id;

-- 6. Function to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_user_preferences_updated_at
    BEFORE UPDATE ON user_preferences
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- 7. RLS Policies (if using Supabase auth)
ALTER TABLE user_preferences ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view own preferences" ON user_preferences
    FOR SELECT USING (true);

CREATE POLICY "Users can update own preferences" ON user_preferences
    FOR UPDATE USING (true);

CREATE POLICY "Users can insert own preferences" ON user_preferences
    FOR INSERT WITH CHECK (true);
