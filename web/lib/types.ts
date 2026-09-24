export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  graphql_public: {
    Tables: {
      [_ in never]: never
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      graphql: {
        Args: {
          extensions?: Json
          operationName?: string
          query?: string
          variables?: Json
        }
        Returns: Json
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
  public: {
    Tables: {
      agent_query_log: {
        Row: {
          created_at: string
          duration_ms: number | null
          log_id: number
          query_ref: string
          rejected_by: string | null
          row_count: number | null
          sql_text: string
          verdict: string
        }
        Insert: {
          created_at?: string
          duration_ms?: number | null
          log_id?: number
          query_ref: string
          rejected_by?: string | null
          row_count?: number | null
          sql_text: string
          verdict: string
        }
        Update: {
          created_at?: string
          duration_ms?: number | null
          log_id?: number
          query_ref?: string
          rejected_by?: string | null
          row_count?: number | null
          sql_text?: string
          verdict?: string
        }
        Relationships: []
      }
      agent_usage: {
        Row: {
          cache_read_tokens: number
          cache_write_tokens: number
          conversations: number
          cost_usd: number
          day: string
          input_tokens: number
          output_tokens: number
          updated_at: string
        }
        Insert: {
          cache_read_tokens?: number
          cache_write_tokens?: number
          conversations?: number
          cost_usd?: number
          day: string
          input_tokens?: number
          output_tokens?: number
          updated_at?: string
        }
        Update: {
          cache_read_tokens?: number
          cache_write_tokens?: number
          conversations?: number
          cost_usd?: number
          day?: string
          input_tokens?: number
          output_tokens?: number
          updated_at?: string
        }
        Relationships: []
      }
      calibration_runs: {
        Row: {
          computed_at: string
          model_version: string
          report: Json
          run_id: number
        }
        Insert: {
          computed_at?: string
          model_version: string
          report: Json
          run_id?: number
        }
        Update: {
          computed_at?: string
          model_version?: string
          report?: Json
          run_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "calibration_runs_model_version_fkey"
            columns: ["model_version"]
            isOneToOne: false
            referencedRelation: "model_versions"
            referencedColumns: ["model_version"]
          },
        ]
      }
      deliveries: {
        Row: {
          ball_in_over: number
          batter_id: number | null
          batting_team_id: number
          bowler_id: number | null
          bowling_team_id: number
          delivery_id: number
          extra_type: string | null
          innings: number
          is_super_over: boolean
          legal_ball_num: number
          match_date: string
          match_id: number
          non_striker_id: number | null
          over_num: number
          player_out_id: number | null
          runs_batter: number
          runs_extras: number
          wicket_count: number
          wicket_type: string | null
        }
        Insert: {
          ball_in_over: number
          batter_id?: number | null
          batting_team_id: number
          bowler_id?: number | null
          bowling_team_id: number
          delivery_id?: number
          extra_type?: string | null
          innings: number
          is_super_over?: boolean
          legal_ball_num: number
          match_date: string
          match_id: number
          non_striker_id?: number | null
          over_num: number
          player_out_id?: number | null
          runs_batter?: number
          runs_extras?: number
          wicket_count?: number
          wicket_type?: string | null
        }
        Update: {
          ball_in_over?: number
          batter_id?: number | null
          batting_team_id?: number
          bowler_id?: number | null
          bowling_team_id?: number
          delivery_id?: number
          extra_type?: string | null
          innings?: number
          is_super_over?: boolean
          legal_ball_num?: number
          match_date?: string
          match_id?: number
          non_striker_id?: number | null
          over_num?: number
          player_out_id?: number | null
          runs_batter?: number
          runs_extras?: number
          wicket_count?: number
          wicket_type?: string | null
        }
        Relationships: [
          {
            foreignKeyName: "deliveries_batter_id_fkey"
            columns: ["batter_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
          {
            foreignKeyName: "deliveries_batting_team_id_fkey"
            columns: ["batting_team_id"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
          {
            foreignKeyName: "deliveries_bowler_id_fkey"
            columns: ["bowler_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
          {
            foreignKeyName: "deliveries_bowling_team_id_fkey"
            columns: ["bowling_team_id"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
          {
            foreignKeyName: "deliveries_match_id_fkey"
            columns: ["match_id"]
            isOneToOne: false
            referencedRelation: "matches"
            referencedColumns: ["match_id"]
          },
          {
            foreignKeyName: "deliveries_non_striker_id_fkey"
            columns: ["non_striker_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
          {
            foreignKeyName: "deliveries_player_out_id_fkey"
            columns: ["player_out_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
        ]
      }
      elo_asof_summary: {
        Row: {
          effective_date: string
          format: string
          rating: number
          team_id: number
        }
        Insert: {
          effective_date: string
          format: string
          rating: number
          team_id: number
        }
        Update: {
          effective_date?: string
          format?: string
          rating?: number
          team_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "elo_asof_summary_team_id_fkey"
            columns: ["team_id"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
        ]
      }
      elo_ratings: {
        Row: {
          as_of: string
          elo_id: number
          format: string
          match_id: number
          rating: number
          team_id: number
        }
        Insert: {
          as_of: string
          elo_id?: number
          format: string
          match_id: number
          rating: number
          team_id: number
        }
        Update: {
          as_of?: string
          elo_id?: number
          format?: string
          match_id?: number
          rating?: number
          team_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "elo_ratings_match_id_fkey"
            columns: ["match_id"]
            isOneToOne: false
            referencedRelation: "matches"
            referencedColumns: ["match_id"]
          },
          {
            foreignKeyName: "elo_ratings_team_id_fkey"
            columns: ["team_id"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
        ]
      }
      match_states: {
        Row: {
          balls_bowled: number
          balls_remaining: number
          balls_since_wicket: number | null
          batter_balls_faced: number | null
          batter_runs_so_far: number | null
          batting_team_won: boolean | null
          current_run_rate: number | null
          delivery_id: number
          dls_resources_pct: number | null
          has_reconciliation_anomaly: boolean
          innings: number
          is_dls_decided: boolean
          match_date: string
          match_id: number
          partnership_balls: number | null
          partnership_runs: number | null
          phase: string
          required_run_rate: number | null
          rrr_minus_crr: number | null
          runs_required: number | null
          score: number
          target: number | null
          wickets: number
        }
        Insert: {
          balls_bowled: number
          balls_remaining: number
          balls_since_wicket?: number | null
          batter_balls_faced?: number | null
          batter_runs_so_far?: number | null
          batting_team_won?: boolean | null
          current_run_rate?: number | null
          delivery_id: number
          dls_resources_pct?: number | null
          has_reconciliation_anomaly?: boolean
          innings: number
          is_dls_decided?: boolean
          match_date: string
          match_id: number
          partnership_balls?: number | null
          partnership_runs?: number | null
          phase: string
          required_run_rate?: number | null
          rrr_minus_crr?: number | null
          runs_required?: number | null
          score: number
          target?: number | null
          wickets: number
        }
        Update: {
          balls_bowled?: number
          balls_remaining?: number
          balls_since_wicket?: number | null
          batter_balls_faced?: number | null
          batter_runs_so_far?: number | null
          batting_team_won?: boolean | null
          current_run_rate?: number | null
          delivery_id?: number
          dls_resources_pct?: number | null
          has_reconciliation_anomaly?: boolean
          innings?: number
          is_dls_decided?: boolean
          match_date?: string
          match_id?: number
          partnership_balls?: number | null
          partnership_runs?: number | null
          phase?: string
          required_run_rate?: number | null
          rrr_minus_crr?: number | null
          runs_required?: number | null
          score?: number
          target?: number | null
          wickets?: number
        }
        Relationships: [
          {
            foreignKeyName: "match_states_delivery_id_fkey"
            columns: ["delivery_id"]
            isOneToOne: true
            referencedRelation: "deliveries"
            referencedColumns: ["delivery_id"]
          },
        ]
      }
      matches: {
        Row: {
          competition: string
          external_ids: Json
          format: string
          has_reconciliation_anomaly: boolean
          match_id: number
          result_method: string | null
          start_time: string
          status: string
          target_overs: number | null
          target_runs: number | null
          team_a: number | null
          team_b: number | null
          toss_decision: string | null
          toss_winner: number | null
          venue_id: number | null
          winner: number | null
        }
        Insert: {
          competition: string
          external_ids?: Json
          format: string
          has_reconciliation_anomaly?: boolean
          match_id?: number
          result_method?: string | null
          start_time: string
          status: string
          target_overs?: number | null
          target_runs?: number | null
          team_a?: number | null
          team_b?: number | null
          toss_decision?: string | null
          toss_winner?: number | null
          venue_id?: number | null
          winner?: number | null
        }
        Update: {
          competition?: string
          external_ids?: Json
          format?: string
          has_reconciliation_anomaly?: boolean
          match_id?: number
          result_method?: string | null
          start_time?: string
          status?: string
          target_overs?: number | null
          target_runs?: number | null
          team_a?: number | null
          team_b?: number | null
          toss_decision?: string | null
          toss_winner?: number | null
          venue_id?: number | null
          winner?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "matches_team_a_fkey"
            columns: ["team_a"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
          {
            foreignKeyName: "matches_team_b_fkey"
            columns: ["team_b"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
          {
            foreignKeyName: "matches_toss_winner_fkey"
            columns: ["toss_winner"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
          {
            foreignKeyName: "matches_venue_id_fkey"
            columns: ["venue_id"]
            isOneToOne: false
            referencedRelation: "venues"
            referencedColumns: ["venue_id"]
          },
          {
            foreignKeyName: "matches_winner_fkey"
            columns: ["winner"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
        ]
      }
      model_versions: {
        Row: {
          artifact_path: string
          is_active: boolean
          is_shadow: boolean
          model_type: string
          model_version: string
          notes: string | null
          test_brier: number | null
          test_log_loss: number | null
          train_end_date: string
          trained_at: string
        }
        Insert: {
          artifact_path: string
          is_active?: boolean
          is_shadow?: boolean
          model_type: string
          model_version: string
          notes?: string | null
          test_brier?: number | null
          test_log_loss?: number | null
          train_end_date: string
          trained_at: string
        }
        Update: {
          artifact_path?: string
          is_active?: boolean
          is_shadow?: boolean
          model_type?: string
          model_version?: string
          notes?: string | null
          test_brier?: number | null
          test_log_loss?: number | null
          train_end_date?: string
          trained_at?: string
        }
        Relationships: []
      }
      player_aliases: {
        Row: {
          alias_id: number
          player_id: number | null
          source: string
          source_id: string | null
          source_name: string
        }
        Insert: {
          alias_id?: number
          player_id?: number | null
          source: string
          source_id?: string | null
          source_name: string
        }
        Update: {
          alias_id?: number
          player_id?: number | null
          source?: string
          source_id?: string | null
          source_name?: string
        }
        Relationships: [
          {
            foreignKeyName: "player_aliases_player_id_fkey"
            columns: ["player_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
        ]
      }
      player_career_summary: {
        Row: {
          bat_balls: number
          bat_fours: number
          bat_innings: number
          bat_outs: number
          bat_runs: number
          bat_sixes: number
          bowl_balls: number
          bowl_runs: number
          bowl_wickets: number
          format: string
          phase: string
          player_id: number
        }
        Insert: {
          bat_balls: number
          bat_fours: number
          bat_innings: number
          bat_outs: number
          bat_runs: number
          bat_sixes: number
          bowl_balls: number
          bowl_runs: number
          bowl_wickets: number
          format: string
          phase: string
          player_id: number
        }
        Update: {
          bat_balls?: number
          bat_fours?: number
          bat_innings?: number
          bat_outs?: number
          bat_runs?: number
          bat_sixes?: number
          bowl_balls?: number
          bowl_runs?: number
          bowl_wickets?: number
          format?: string
          phase?: string
          player_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "player_career_summary_player_id_fkey"
            columns: ["player_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
        ]
      }
      player_index: {
        Row: {
          bat_innings: number
          bat_runs: number
          bowl_innings: number
          bowl_wickets: number
          canonical_name: string
          formats: string
          matches: number
          normalized_name: string
          player_id: number
          surname_key: string
        }
        Insert: {
          bat_innings: number
          bat_runs: number
          bowl_innings: number
          bowl_wickets: number
          canonical_name: string
          formats: string
          matches: number
          normalized_name: string
          player_id: number
          surname_key: string
        }
        Update: {
          bat_innings?: number
          bat_runs?: number
          bowl_innings?: number
          bowl_wickets?: number
          canonical_name?: string
          formats?: string
          matches?: number
          normalized_name?: string
          player_id?: number
          surname_key?: string
        }
        Relationships: [
          {
            foreignKeyName: "player_index_player_id_fkey"
            columns: ["player_id"]
            isOneToOne: true
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
        ]
      }
      player_state: {
        Row: {
          as_of: string
          bat_ability_mean: number | null
          bat_ability_sd: number | null
          bowl_ability_mean: number | null
          bowl_ability_sd: number | null
          format: string
          innings_observed: number
          player_id: number
        }
        Insert: {
          as_of: string
          bat_ability_mean?: number | null
          bat_ability_sd?: number | null
          bowl_ability_mean?: number | null
          bowl_ability_sd?: number | null
          format: string
          innings_observed: number
          player_id: number
        }
        Update: {
          as_of?: string
          bat_ability_mean?: number | null
          bat_ability_sd?: number | null
          bowl_ability_mean?: number | null
          bowl_ability_sd?: number | null
          format?: string
          innings_observed?: number
          player_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "player_state_player_id_fkey"
            columns: ["player_id"]
            isOneToOne: false
            referencedRelation: "players"
            referencedColumns: ["player_id"]
          },
        ]
      }
      players: {
        Row: {
          batting_hand: string | null
          bowling_style: string | null
          canonical_name: string
          dob: string | null
          player_id: number
        }
        Insert: {
          batting_hand?: string | null
          bowling_style?: string | null
          canonical_name: string
          dob?: string | null
          player_id?: number
        }
        Update: {
          batting_hand?: string | null
          bowling_style?: string | null
          canonical_name?: string
          dob?: string | null
          player_id?: number
        }
        Relationships: []
      }
      prediction_outcomes: {
        Row: {
          actual: Json
          brier: number | null
          log_loss: number | null
          prediction_id: number
          resolved_at: string
        }
        Insert: {
          actual: Json
          brier?: number | null
          log_loss?: number | null
          prediction_id: number
          resolved_at?: string
        }
        Update: {
          actual?: Json
          brier?: number | null
          log_loss?: number | null
          prediction_id?: number
          resolved_at?: string
        }
        Relationships: [
          {
            foreignKeyName: "prediction_outcomes_prediction_id_fkey"
            columns: ["prediction_id"]
            isOneToOne: true
            referencedRelation: "predictions"
            referencedColumns: ["prediction_id"]
          },
        ]
      }
      predictions: {
        Row: {
          ball_in_over: number | null
          created_at: string
          delivery_id: number | null
          innings: number | null
          match_id: number
          match_phase: string
          model_version: string
          over_num: number | null
          payload: Json
          prediction_id: number
          prediction_type: string
          source: string
          subject_id: number | null
        }
        Insert: {
          ball_in_over?: number | null
          created_at?: string
          delivery_id?: number | null
          innings?: number | null
          match_id: number
          match_phase: string
          model_version: string
          over_num?: number | null
          payload: Json
          prediction_id?: number
          prediction_type: string
          source?: string
          subject_id?: number | null
        }
        Update: {
          ball_in_over?: number | null
          created_at?: string
          delivery_id?: number | null
          innings?: number | null
          match_id?: number
          match_phase?: string
          model_version?: string
          over_num?: number | null
          payload?: Json
          prediction_id?: number
          prediction_type?: string
          source?: string
          subject_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "predictions_delivery_id_fkey"
            columns: ["delivery_id"]
            isOneToOne: false
            referencedRelation: "deliveries"
            referencedColumns: ["delivery_id"]
          },
          {
            foreignKeyName: "predictions_match_id_fkey"
            columns: ["match_id"]
            isOneToOne: false
            referencedRelation: "matches"
            referencedColumns: ["match_id"]
          },
          {
            foreignKeyName: "predictions_model_version_fkey"
            columns: ["model_version"]
            isOneToOne: false
            referencedRelation: "model_versions"
            referencedColumns: ["model_version"]
          },
        ]
      }
      reference_sync_state: {
        Row: {
          content_hash: string
          rebuilt_at: string
          row_count: number
          synced_at: string | null
          table_name: string
        }
        Insert: {
          content_hash: string
          rebuilt_at: string
          row_count: number
          synced_at?: string | null
          table_name: string
        }
        Update: {
          content_hash?: string
          rebuilt_at?: string
          row_count?: number
          synced_at?: string | null
          table_name?: string
        }
        Relationships: []
      }
      team_aliases: {
        Row: {
          alias_id: number
          source: string
          source_id: string | null
          source_name: string
          team_id: number | null
        }
        Insert: {
          alias_id?: number
          source: string
          source_id?: string | null
          source_name: string
          team_id?: number | null
        }
        Update: {
          alias_id?: number
          source?: string
          source_id?: string | null
          source_name?: string
          team_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "team_aliases_team_id_fkey"
            columns: ["team_id"]
            isOneToOne: false
            referencedRelation: "teams"
            referencedColumns: ["team_id"]
          },
        ]
      }
      teams: {
        Row: {
          name: string
          short_name: string | null
          team_id: number
        }
        Insert: {
          name: string
          short_name?: string | null
          team_id?: number
        }
        Update: {
          name?: string
          short_name?: string | null
          team_id?: number
        }
        Relationships: []
      }
      unresolved_entities: {
        Row: {
          candidates: Json
          created_at: string
          entity_kind: string
          first_seen_match_id: number | null
          reason: string
          source: string
          source_id: string | null
          source_name: string
          status: string
          unresolved_id: number
        }
        Insert: {
          candidates?: Json
          created_at?: string
          entity_kind: string
          first_seen_match_id?: number | null
          reason?: string
          source: string
          source_id?: string | null
          source_name: string
          status?: string
          unresolved_id?: number
        }
        Update: {
          candidates?: Json
          created_at?: string
          entity_kind?: string
          first_seen_match_id?: number | null
          reason?: string
          source?: string
          source_id?: string | null
          source_name?: string
          status?: string
          unresolved_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "unresolved_entities_first_seen_match_id_fkey"
            columns: ["first_seen_match_id"]
            isOneToOne: false
            referencedRelation: "matches"
            referencedColumns: ["match_id"]
          },
        ]
      }
      venue_aliases: {
        Row: {
          alias_id: number
          source: string
          source_id: string | null
          source_name: string
          venue_id: number | null
        }
        Insert: {
          alias_id?: number
          source: string
          source_id?: string | null
          source_name: string
          venue_id?: number | null
        }
        Update: {
          alias_id?: number
          source?: string
          source_id?: string | null
          source_name?: string
          venue_id?: number | null
        }
        Relationships: [
          {
            foreignKeyName: "venue_aliases_venue_id_fkey"
            columns: ["venue_id"]
            isOneToOne: false
            referencedRelation: "venues"
            referencedColumns: ["venue_id"]
          },
        ]
      }
      venue_asof_summary: {
        Row: {
          chase_n: number
          chase_wins: number
          effective_date: string
          first_inns_n: number
          first_inns_runs: number
          venue_id: number
        }
        Insert: {
          chase_n: number
          chase_wins: number
          effective_date: string
          first_inns_n: number
          first_inns_runs: number
          venue_id: number
        }
        Update: {
          chase_n?: number
          chase_wins?: number
          effective_date?: string
          first_inns_n?: number
          first_inns_runs?: number
          venue_id?: number
        }
        Relationships: [
          {
            foreignKeyName: "venue_asof_summary_venue_id_fkey"
            columns: ["venue_id"]
            isOneToOne: false
            referencedRelation: "venues"
            referencedColumns: ["venue_id"]
          },
        ]
      }
      venues: {
        Row: {
          city: string | null
          country: string | null
          name: string
          venue_id: number
        }
        Insert: {
          city?: string | null
          country?: string | null
          name: string
          venue_id?: number
        }
        Update: {
          city?: string | null
          country?: string | null
          name?: string
          venue_id?: number
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      [_ in never]: never
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends (DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never) = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends (DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never) = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends (PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never) = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  graphql_public: {
    Enums: {},
  },
  public: {
    Enums: {},
  },
} as const
