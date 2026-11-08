#!/usr/bin/env python3
"""Build a reproducible Phase 2 analysis bundle from Phase 1 metrics/per-clip CSV."""
from __future__ import annotations
import argparse, csv, json, math
from pathlib import Path
from collections import Counter
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--metrics', type=Path, required=True)
    parser.add_argument('--per-clip', type=Path, required=True)
    parser.add_argument('--labels', type=Path, default=ROOT/'config'/'label_map_include50.json')
    parser.add_argument('--out-dir', type=Path, required=True)
    args=parser.parse_args()
    metrics=json.loads(args.metrics.read_text(encoding='utf-8-sig'))
    df=pd.read_csv(args.per_clip)
    labels=json.loads(args.labels.read_text(encoding='utf-8-sig'))
    inv={i:k for k,i in labels.items()}
    if metrics.get('sample_count') != 77 or len(df)!=77 or len(labels)!=50:
        raise SystemExit('Expected complete Phase 1 evidence: 77 metrics rows, 77 CSV rows, 50 classes.')
    if metrics.get('manifest',{}).get('test_split_used') is not False:
        raise SystemExit('Test split must not be used in Phase 2 report.')
    out=args.out_dir.resolve(); out.mkdir(parents=True,exist_ok=True)
    recog=metrics['recognition_metrics']
    browser=recog['browser_canvas']; raw=recog['opencv_native_reference']
    y=df['label'].astype(str); b=df['browser_prediction'].astype(str); r=df['raw_prediction'].astype(str)
    support=df.groupby('label').size()
    browser_correct=(b==y)
    raw_correct=(r==y)
    both_correct=(browser_correct & raw_correct)
    raw_only=((~browser_correct)&raw_correct)
    browser_only=(browser_correct & (~raw_correct))
    both_wrong=((~browser_correct)&(~raw_correct))
    same=(b==r)
    frame=metrics['frame_summary']
    def desc(series):
        x=pd.to_numeric(series,errors='coerce').dropna()
        return { 'count':int(len(x)), 'mean':float(x.mean()), 'median':float(x.median()),
                 'p90':float(x.quantile(.9)), 'p95':float(x.quantile(.95)), 'max':float(x.max()) }
    group_stats={c:desc(df[c]) for c in ['feature_mad','pose_feature_mad','hand1_feature_mad','hand2_feature_mad','padded_sequence_mad','logit_mad','probability_mad']}
    browser_errors=df[b!=y][['video_path','label','browser_prediction','browser_confidence']].to_dict('records')
    top_confusions=[]
    cm=browser['confusion_matrix']
    for i,row in enumerate(cm):
        for j,count in enumerate(row):
            if i!=j and count:
                top_confusions.append({'actual':inv[i],'predicted':inv[j],'count':int(count)})
    top_confusions.sort(key=lambda x:(-x['count'],x['actual'],x['predicted']))
    summaries={}
    for branch,data in [('browser_canvas',browser),('opencv_native_reference',raw)]:
        summaries[branch]={
            'top1_correct':int(round(data['top1_accuracy_on_predicted']*77)),
            'top1_accuracy':data['top1_accuracy_on_predicted'],
            'top5_correct':int(round(data['top5_accuracy_on_predicted']*77)),
            'top5_accuracy':data['top5_accuracy_on_predicted'],
            'macro_f1':data['macro_f1_on_predicted'],
            'weighted_f1':data['weighted_f1_on_predicted'],
            'accepted_at_0_40':data['accepted_count_at_text_threshold'],
            'coverage_at_0_40':data['coverage_at_text_threshold_over_77'],
            'selective_accuracy_at_0_40':data['selective_accuracy_at_text_threshold'],
            'confidence':{'min':data['confidence_min'],'median':data['confidence_median'],'max':data['confidence_max']},
        }
    def wilson_interval(correct, total, z=1.96):
        p=float(correct)/float(total)
        den=1.0+(z*z)/total
        centre=(p+(z*z)/(2.0*total))/den
        half=z*math.sqrt((p*(1.0-p)/total)+(z*z/(4.0*total*total)))/den
        return [centre-half, centre+half]
    discordant_only_raw=int(raw_only.sum())
    discordant_only_browser=int(browser_only.sum())
    discordant=discordant_only_raw+discordant_only_browser
    if discordant:
        tail=sum(math.comb(discordant,k) for k in range(min(discordant_only_raw,discordant_only_browser)+1))/(2**discordant)
        mcnemar_exact_two_sided=min(1.0,2.0*tail)
    else:
        mcnemar_exact_two_sided=1.0
    diagnostic_associations={}
    browser_error=(~browser_correct).astype(int)
    prediction_disagreement=(~same).astype(int)
    for column in ['feature_mad','pose_feature_mad','hand1_feature_mad','hand2_feature_mad','padded_sequence_mad','logit_mad','probability_mad']:
        diagnostic_associations[column]={
            'spearman_with_browser_error':float(df[column].corr(browser_error,method='spearman')) if df[column].notna().sum()>2 else None,
            'spearman_with_prediction_disagreement':float(df[column].corr(prediction_disagreement,method='spearman')) if df[column].notna().sum()>2 else None,
            'warning':'Exploratory descriptive association on 77 validation clips; not causal and not a reliable inferential estimate.'
        }
    summary={
        'campaign':'ISL Phase 2 root-cause analysis of Phase 1 evidence',
        'source_metrics_generated_at_utc':metrics.get('generated_at_utc'),
        'sample_count':77,'class_count':50,
        'class_support_distribution':{str(int(k)):int(v) for k,v in support.value_counts().sort_index().items()},
        'classes_with_support_1':int((support==1).sum()),
        'classes_with_support_2':int((support==2).sum()),
        'recognition':summaries,
        'paired_outcomes':{
            'same_top1_predictions':int(same.sum()),
            'same_top1_rate':float(same.mean()),
            'both_correct':int(both_correct.sum()),
            'only_browser_correct':int(browser_only.sum()),
            'only_opencv_correct':int(raw_only.sum()),
            'both_wrong':int(both_wrong.sum()),
            'browser_top1_wilson_95_percent_interval':wilson_interval(int(browser_correct.sum()),len(df)),
            'opencv_top1_wilson_95_percent_interval':wilson_interval(int(raw_correct.sum()),len(df)),
            'mcnemar_exact_two_sided_p_value':mcnemar_exact_two_sided,
            'mcnemar_discordant_only_opencv_correct':discordant_only_raw,
            'mcnemar_discordant_only_browser_correct':discordant_only_browser,
        },
        'frames':frame,
        'coverage_means':{
            'browser_pose_ratio':float(df.browser_pose_ratio.mean()),
            'opencv_pose_ratio':float(df.raw_pose_ratio.mean()),
            'browser_any_hand_ratio':float(df.browser_hand_ratio.mean()),
            'opencv_any_hand_ratio':float(df.raw_hand_ratio.mean()),
        },
        'feature_difference_distribution':group_stats,
        'exploratory_spearman_associations':diagnostic_associations,
        'prediction_disagreements':df.loc[~same,['video_path','label','browser_prediction','raw_prediction','browser_confidence','raw_confidence','pose_feature_mad','hand1_feature_mad','hand2_feature_mad','padded_sequence_mad','logit_mad','top5_overlap_count']].to_dict('records'),
        'browser_misclassified_examples':browser_errors,
        'most_common_browser_confusions':top_confusions[:20],
        'risk_coverage_browser':browser['risk_coverage_curve'],
        'risk_coverage_opencv':raw['risk_coverage_curve'],
        'provenance':{
            'ai4bharat_commit':metrics.get('assets',{}).get('repo_head'),
            'checkpoint_sha256':metrics.get('assets',{}).get('checkpoint_sha256'),
            'official_validation_list_sha256':metrics.get('manifest',{}).get('official_validation_list_sha256'),
            'mediapipe_runtime':'0.10.21 (from target Windows pip installation log)',
            'historical_mediapipe_version':'UNVERIFIED / NOT PINNED IN UPSTREAM REQUIREMENTS',
            'iot_enabled':metrics.get('contract',{}).get('iot_enabled'),
            'test_split_used':metrics.get('manifest',{}).get('test_split_used'),
        },
    }
    (out/'phase2_analysis.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')

    # Chart 1: recognition metrics
    names=['Top-1 accuracy','Top-5 accuracy','Macro F1']
    x=np.arange(len(names)); width=.36
    fig,ax=plt.subplots(figsize=(8,4.8))
    ax.bar(x-width/2,[browser['top1_accuracy_on_predicted'],browser['top5_accuracy_on_predicted'],browser['macro_f1_on_predicted']],width,label='Browser Canvas')
    ax.bar(x+width/2,[raw['top1_accuracy_on_predicted'],raw['top5_accuracy_on_predicted'],raw['macro_f1_on_predicted']],width,label='OpenCV native reference')
    ax.set_xticks(x,names); ax.set_ylim(0,1); ax.set_ylabel('Score'); ax.set_title('Phase 1 recognition baseline (77 official validation clips)'); ax.legend(); ax.grid(axis='y',alpha=.25)
    fig.tight_layout(); fig.savefig(out/'recognition_baseline.png',dpi=170); plt.close(fig)

    # Chart 2: per-clip feature MAD distribution
    data=[df['pose_feature_mad'].dropna(),df['hand1_feature_mad'].dropna(),df['hand2_feature_mad'].dropna()]
    fig,ax=plt.subplots(figsize=(8,4.8)); ax.boxplot(data,tick_labels=['Pose 50-D','Hand slot 1 42-D','Hand slot 2 42-D'],showfliers=True)
    ax.set_ylabel('Mean absolute feature difference (source pixel coordinates)'); ax.set_title('Per-clip feature difference: Browser Canvas vs native OpenCV'); ax.grid(axis='y',alpha=.25)
    fig.tight_layout(); fig.savefig(out/'feature_group_mad.png',dpi=170); plt.close(fig)

    # Chart 3: validation risk/coverage; explicitly exploratory, not calibration.
    fig,ax=plt.subplots(figsize=(8,4.8))
    for branch_name,data,label in [('browser_canvas',browser,'Browser Canvas'),('opencv_native_reference',raw,'OpenCV native')]:
        curve=data['risk_coverage_curve']
        ax.plot([z['coverage_over_77'] for z in curve],[z['selective_accuracy'] for z in curve],marker='o',label=label)
    ax.set_xlabel('Coverage over 77'); ax.set_ylabel('Selective accuracy'); ax.set_ylim(0,1.05); ax.set_title('Exploratory confidence risk–coverage (thresholds not calibrated)'); ax.legend(); ax.grid(alpha=.25)
    fig.tight_layout(); fig.savefig(out/'risk_coverage_exploratory.png',dpi=170); plt.close(fig)

    # Chart 4: prediction agreement outcomes
    outcome_labels=['Both correct','Browser only correct','OpenCV only correct','Both wrong']
    outcome_values=[int(both_correct.sum()),int(browser_only.sum()),int(raw_only.sum()),int(both_wrong.sum())]
    fig,ax=plt.subplots(figsize=(8,4.8)); ax.bar(outcome_labels,outcome_values)
    ax.set_ylabel('Validation clips'); ax.set_title('Paired prediction outcomes'); ax.tick_params(axis='x',rotation=15); ax.grid(axis='y',alpha=.25)
    fig.tight_layout(); fig.savefig(out/'paired_prediction_outcomes.png',dpi=170); plt.close(fig)

    # Chart 5: most frequent browser confusions
    errors=top_confusions[:12]
    fig,ax=plt.subplots(figsize=(9,5.5))
    if errors:
        labels2=[f"{z['actual']} → {z['predicted']}" for z in errors]
        ax.barh(labels2[::-1],[z['count'] for z in errors][::-1])
    ax.set_xlabel('Count'); ax.set_title('Most frequent Browser Canvas class confusions'); ax.grid(axis='x',alpha=.25)
    fig.tight_layout(); fig.savefig(out/'top_confusions.png',dpi=170); plt.close(fig)

    csv_rows=[]
    for row in summary['prediction_disagreements']:
        csv_rows.append(row)
    if csv_rows:
        pd.DataFrame(csv_rows).to_csv(out/'prediction_disagreements.csv',index=False)
    print(json.dumps({k:v for k,v in summary.items() if k not in ('prediction_disagreements','browser_misclassified_examples','risk_coverage_browser','risk_coverage_opencv')},indent=2))
    print('Outputs:',out)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
