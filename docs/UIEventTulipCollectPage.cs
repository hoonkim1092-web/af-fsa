using SMGS;
using System.Collections;
using System.Collections.Generic;
using UnityEngine;
using EnhancedUI.EnhancedScroller;
using UnityEngine.UI;
using TMPro;
using System;
using UnityEngine.Events;
using System.Linq;
using DG.Tweening;

public class UIEventTulipCollectPage : UIEventBase<GED_Collect>
{
    EnhancedListView collectScrollView;
    UICollectRewardItem collectRewardItem;
    UIEventCollectProgressItem collectProgressItem;

    SlicedFilledImage _collectTargetProgressVar { get; set; }
    TextMeshProUGUI _collectProgressValText;
    GameObject _progressBarAnimation;

    private readonly List<GED_Collect.CollectData> _collectDatas = new List<GED_Collect.CollectData>();

    private readonly List<GED_Collect.CollectData> _reversecollectDatas = new List<GED_Collect.CollectData>();

    private Dictionary<int, int> _cachedCellItemIndexByStepDic = new Dictionary<int, int>();
    //CharacterUI _characterUI = null;
    SpineObjUI _smBearUI = null;
    bool isAnimationCollect = false;
    public Transform transStartPosition;

    bool _isReservationReward = false;
    
    public override void Awake()
    {
        base.Awake();
        if (Controller.Background)
            Controller.Background.OnLoaded += OnLoadedBackground;

        this.baseUi.OnClosedCompleted += OnClosedCompleted;
        this.baseUi.OnShowedCompleted += OnShowCompleted;
    }

    private void OnLoadedBackground()
    {
        //_characterUI = FindComponent<CharacterUI>("AvatarUI");
        //_characterUI.InitUserCharacter((anim) => _characterUI.PlayDefaultAnim());
        SetData();
    }

    public void OnClosedCompleted()
    {
        GameLog.Debug("UIEventTulipCollectPage **************** OnClosedCompleted!!!");

        Data.IsReservationReward = _isReservationReward;
        //_characterUI.RemoveCharacter(false);
    }

    protected override void OnDestroy()
    {
        base.OnDestroy();
        //_characterUI.RemoveCharacter(true);
    }
#if UNITY_EDITOR
	private void Update()
	{
        if (Input.GetKeyUp(KeyCode.X))
        {
            UIMinizCollectPageClear.ShowPopup(Data, () => { Close(); });
        }
	}
#endif
	public override void InitComponent()
    {
        base.InitComponent();
        collectScrollView = FindComponent<EnhancedListView>("ListViewItem");
        collectRewardItem = FindComponent<UICollectRewardItem>("UIEventCollectRewardItem");
        _collectTargetProgressVar = FindComponent<SlicedFilledImage>("Image ProgressGage");
        _collectProgressValText = FindComponent<TextMeshProUGUI>("Text (TMP) LevelProgress");

        collectProgressItem = FindComponent<UIEventCollectProgressItem>("UIEventCollectProgressItem");
        collectRewardItem.gameObject.SetActive(false);
        _smBearUI = FindComponent<SpineObjUI>("SMBear");
        _progressBarAnimation = FindGameObject("EngaugeFx");
        transStartPosition = FindComponent<Transform>("Spine");
    }

    protected override void OnShowUI()
    {
        base.OnShowUI();
    }

    private void SetData()
    {
		Data._lastUpdateIndex = 0;
        
		_collectDatas.Clear();
        _cachedCellItemIndexByStepDic.Clear();
        _collectDatas.AddRange( Data.Collects );
        _reversecollectDatas.AddRange(Data.Collects);
        _collectDatas.Sort((x, y) => y.Step.CompareTo(x.Step));
		//collectScrollView.Init(_collectDatas.Count, OnUpdateItem);
		float position = 1f - ((Data.CurrentStep - 1f) / (_collectDatas.Count - 1f));

		collectScrollView.InitWithPosition(_collectDatas.Count, OnUpdateItem, position);


		for (int i = _collectDatas.Count - 1; i >= 0; --i)
        {
            _cachedCellItemIndexByStepDic.Add(_collectDatas[i].Step, i);
        }
       
        UpdateCollectUi();

		//collectScrollView.scroller.JumpToDataIndex(_cachedCellItemIndexByStepDic[Data.CurrentStep], 0, -2f, true, EnhancedScroller.TweenType.immediate, 0f, () =>
		//{
		//    var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[Data.CurrentStep]).GetComponentInChildren<UICollectRewardItem>();
		//    cellView.tranTargetPostion.gameObject.SetActive(true);
		//    this.NextFrame(() =>
		//    {               
		//        transStartPosition.position = cellView.tranTargetPostion.position;
		//        _smBearUI.transform.SetParent(cellView.tranTargetPostion, false);
		//        _smBearUI.GetRectTransform().anchoredPosition3D = Vector3.zero;
		//    });            
		//}
		//);
		var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[Data.CurrentStep]).GetComponentInChildren<UICollectRewardItem>();
        cellView.tranTargetPostion.gameObject.SetActive(true);
        this.NextFrame(() =>
        {
            transStartPosition.position = cellView.tranTargetPostion.position;
            _smBearUI.transform.SetParent(cellView.tranTargetPostion, false);
            _smBearUI.GetRectTransform().anchoredPosition3D = Vector3.zero;
        });
    }

	void OnShowCompleted()
    {
        this.NextFrame(() =>
        {
            if (Data.NeedUpdateData && Data.Result.CollectEvent.Collect != null)
            {
                isAnimationCollect = true;
                _isReservationReward = Data.CanReceiveAnyReward();
                Game.TouchGuardManager.Lock();
                var actions = ActionList.New();

                SMBearStartPosition();

                var _currDatas = _reversecollectDatas.FindAll(x => x.NeedUpdate);
                //actions.Add(onEnd => SetSMBearMoveRandomPlay(onEnd));
                foreach (var _currData in _currDatas)
                {
                    GameLog.Debug($"Event Collect Add Data Step {_currData.Step} updateProgressCount {_currData.GetUpdateCollecCount()}");

                    actions.Add(onEnd => StartCoroutine(TotalProgressBarAnimation(_currData, onEnd)));
                }

                actions.Run(OnProgressComplete);
            }
        });       
    }

    void SMBearStartPosition()
    {
        _smBearUI.transform.SetParent(transStartPosition, false);
        _smBearUI.GetRectTransform().anchoredPosition3D = Vector3.zero;
    }

    /// <summary>
    /// 바텀 프로그래스 바 진행.
    /// </summary>
    /// <param name="data"></param>
    /// <param name="onComplete"></param>
    /// <returns></returns>
    private IEnumerator TotalProgressBarAnimation(GED_Collect.CollectData data, Action onComplete)
    {        
        int targetCount = data.UpdateCollecCount;
        float startProgress = data.GetCurrCollectRate();
        float targetProgress = Mathf.Clamp01((float)targetCount / (float)data.Goal);
        float duration = 0.5f; // 애니메이션 시간
        float elapsedTime = 0f;

        // 슬라이더 바 증가 연출
        // 동시 연출 스크롤 아이템.
        //SmBearMoveTween(_smBearUI.transform, data.Step).SetEase(Ease.Linear).OnComplete(() => OnCompleteAnimation(data.Step));

        while (elapsedTime < duration)
        {
            _collectTargetProgressVar.fillAmount = Mathf.Lerp(startProgress, targetProgress, elapsedTime / duration);
            _collectProgressValText.text = $"{(int)Mathf.Lerp(data.CurrCollectCount, targetCount, elapsedTime / duration)}/{data.Goal}";
            elapsedTime += Time.deltaTime;
            yield return null;
        }

        _collectTargetProgressVar.fillAmount = data.GetUpdateCollecCountRate();
        _collectProgressValText.text = $"{targetCount}/{data.Goal}";
        collectProgressItem.SetData(Data.IngameTargetObjectID, Data.GameEvent.GetRewardId(data.Step), data.GetUpdateCollecCountRate());
   
        if (data.IsUpdateComplete())
        {
            _progressBarAnimation.gameObject.SetActive(true);

            float duration1 = 0f;
            DG.Tweening.DOTweenAnimation[] tweens = _progressBarAnimation.GetComponents<DG.Tweening.DOTweenAnimation>();
            foreach (var animation in tweens)
            {
                animation.DORewind();
                animation.DOPause();
                animation.DOPlay();
                duration1 = animation.duration;
            }
            this.DelayInvoke(duration1, () =>
            {
                OnProgressClearComplete();
            });
        }

        onComplete?.Invoke();
    }

    protected DG.Tweening.Tween SmBearMoveTween(Transform item, int targetStep)
    {        
        Vector3 position = Vector3.zero;
		int step = targetStep == 0 ? Data.CurrentStep : targetStep;
        int stepIndex = step == 0 ? 1 : step;
		var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[stepIndex]).GetComponentInChildren<UICollectRewardItem>();
        position = cellView.tranTargetPostion.position;
        Game.SoundManager.PlaySound("sfx_collect_event_move");
        item.DOKill();

        return item.DOMove(position, 0.5f);
    }

    protected void OnCompleteAnimation(int targetStep)
    {
        int step = targetStep == 0 ? Data.CurrentStep : targetStep;
		int stepIndex = step == 0 ? 1 : step;
		GameLog.Debug($"Event Collect OnCompleteAnimation Data.CurrentStep ===  {Data.CurrentStep}");
		GameLog.Debug($"Event Collect OnCompleteAnimation targetStep {targetStep}");
		GameLog.Debug($"Event Collect OnCompleteAnimation stepIndex {stepIndex}");
		var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[stepIndex]).GetComponentInChildren<UICollectRewardItem>();

        if (cellView != null)
        {
			cellView.tranTargetPostion.gameObject.SetActive(true);
			transStartPosition.position = cellView.tranTargetPostion.position;
			_smBearUI.transform.SetParent(cellView.tranTargetPostion, false);
			_smBearUI.GetRectTransform().anchoredPosition3D = Vector3.zero;
		}


        GameLog.Debug($"Event Collect OnCompleteAnimation NextFrame name {cellView.name}");
    }

    void OnProgressComplete()
    {
        var datas = _reversecollectDatas.FindAll(x => x.NeedUpdate == true);
        var actions = ActionList.New();

        float perTime = 0.25f;
        float duration = (float)(perTime * (datas.Count - 1));

        var dataList = datas.ToList();
        int dataCount = dataList.Count;

        int itemsPerPage = collectScrollView.scroller.ActiveCellCount;

        int currentIndex = 0;

        // 데이터가 한 번에 처리할 수 있는 개수인 itemsPerPage 만큼씩 처리
        while (currentIndex < dataCount)
        {
            int endIndex = Mathf.Min(currentIndex + itemsPerPage, dataCount);  // 마지막 인덱스는 dataList의 크기를 넘지 않도록 설정
            var dataSubset = dataList.GetRange(currentIndex, endIndex - currentIndex);  // itemsPerPage 만큼 묶어서 처리
            bool isFirst = (currentIndex == 0); // 첫 번째 데이터
            bool isLast = (currentIndex == dataCount - 1); // 마지막 데이터
            for (int i = 0; i < dataSubset.Count; i++)
            {
                var data = dataSubset[i];

                isFirst = (currentIndex == 0 && i == 0); // 첫 번째 데이터
                GED_Collect.TweenType twType = GED_Collect.TweenType.Linear;
                EnhancedScroller.TweenType scrollTwType = EnhancedScroller.TweenType.linear;
                if (isFirst)
                {
                    twType = GED_Collect.TweenType.EaseInCubic;
                    scrollTwType = EnhancedScroller.TweenType.easeInCubic;
                }

                if (isLast)
                {
                    twType = GED_Collect.TweenType.EaseOutCubic;
                    scrollTwType = EnhancedScroller.TweenType.easeOutCubic;
                }

                GameLog.Debug($"Event Collect OnProgressComplete {data.Step} {data.NeedUpdate} scrollTwType {scrollTwType}");

                actions.Add(onEnd => data.UpdateStepAni(perTime, () =>
                {
                    if (data.Step != Data.GetUpdateStep())
                    {
                        SetSMBearMoveRandomPlay(null);
                    }
                    data.UpdateStepAniFinished();
                    onEnd();
                }, twType));


                if (data.IsUpdateComplete())
                {
                    actions.Add(onEnd =>
                    {
                        //// 애니메이션이 끝난 후에 JumpToDataIndex를 실행
                        //int updateStep = Data.LastStep == data.Step ? Data.LastStep : data.Step + 1;
                        //collectScrollView.scroller.JumpToDataIndex(_cachedCellItemIndexByStepDic[updateStep], 0, -2f, true, scrollTwType, perTime, () =>
                        //{
                        //    var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[updateStep]).GetComponentInChildren<UICollectRewardItem>();
                        //    cellView.tranTargetPostion.gameObject.SetActive(true);

                        //    // Bear 애니메이션
                        //    SmBearMoveTween(_smBearUI.transform, updateStep).SetEase(Ease.Linear).OnComplete(() =>
                        //    {
                        //        //OnCompleteAnimation(updateStep);
                        //    });
                        //});

                        //onEnd();
                        float delayedTime = 0f;
                        if(data.Step + 1 == Data.GetUpdateStep())
                        {
                            delayedTime = 0.2f;
                            Data._lastUpdateIndex = data.Step + 1;
						}
						
						DOVirtual.DelayedCall(delayedTime, () =>
						{
							int updateStep = Data.LastStep == data.Step ? Data.LastStep : data.Step + 1;
                            GameLog.Debug($"Data.LastStep == : {Data.LastStep}  ///   data.Step === : {data.Step}   ///    data.Step + 1 ===== : {data.Step + 1}");
							collectScrollView.scroller.JumpToDataIndex(_cachedCellItemIndexByStepDic[updateStep], 0, -2f, true, scrollTwType, perTime, () =>
							{
								GameLog.Debug("JumpToDataIndex 완료 콜백 진입");

								var cellView = collectScrollView.scroller.GetCellViewAtDataIndex(_cachedCellItemIndexByStepDic[updateStep]).GetComponentInChildren<UICollectRewardItem>();
								cellView.tranTargetPostion.gameObject.SetActive(true);
                                cellView.SetcurrbgOn();

								// Bear 애니메이션
								SmBearMoveTween(_smBearUI.transform, updateStep).SetEase(Ease.Linear).OnComplete(() =>
								{
									//OnCompleteAnimation(updateStep);
									onEnd();
									
								});
							});

							//onEnd(); // 완료 콜백
						});
					});
                }               

                if (Data.LastStep == data.Step && data.IsUpdateComplete())
                {
					actions.Add(onEnd =>
                    {
                        _isReservationReward = false;
                        UIMinizCollectPageClear.ShowPopup(Data, ()=> { Close(); });
                        onEnd();
					});
                }
            }

            currentIndex += itemsPerPage;
        }

        actions.Add(onEnd =>
        {
            Data.AnimationFinished();
            _smBearUI.PlayDefaultAnim();

            int updateStep = Data.GetUpdateStep() == 0 ? Data.CurrentStep : Data.GetUpdateStep();
            OnCompleteAnimation(updateStep);
            Game.TouchGuardManager.Unlock();
            onEnd();
        });

        actions.Run(() =>
        {
			isAnimationCollect = false;
        });
    }

    
    void OnUpdateItem(RectTransform parent, int itemIndex)
    {
        var itemUI = collectRewardItem.SpawnEnhanceItem(parent, collectScrollView, itemIndex);

        itemUI.transform.localScale = Vector3.one;

        var data = _collectDatas[itemIndex];
       Debug.LogWarning($"Event Collect OnUpdateItem Step: ================================= {data.Step}  CurrentStep: {Data.CurrentStep}  itemIndex: {itemIndex}");
		itemUI.SetData(data);

		if (_cachedCellItemIndexByStepDic.Count != 0)
        {
			GameLog.Debug($"_cachedCellItemIndexByStepDic[{Data.CurrentStep}] == : {_cachedCellItemIndexByStepDic[Data.CurrentStep]}  ///   itemIndex === : {itemIndex}");
			itemUI.tranTargetPostion.gameObject.SetActive(_cachedCellItemIndexByStepDic[Data.CurrentStep] == itemIndex);
            if (isAnimationCollect == false && _cachedCellItemIndexByStepDic[Data.CurrentStep] == itemIndex)
            {
                _smBearUI.transform.SetParent(itemUI.tranTargetPostion, false);
                _smBearUI.GetRectTransform().anchoredPosition3D = Vector3.zero;
            }
        }
    }

    void UpdateCollectUi()
    {
        GameLog.Debug($"Event Collect UpdateCollectUi CurrentStep: {Data.CurrentStep}");
        var _currData = _collectDatas.Find(x => x.Step == Data.CurrentStep);
        if (_currData != null)
        {
            _collectTargetProgressVar.fillAmount = _currData.GetCurrCollectRate();
            GameLog.Debug($"Event Collect UpdateCollectUi fillamout:{_collectTargetProgressVar.fillAmount}");
            _collectProgressValText.text = string.Format($"{_currData.CurrCollectCount}/{_currData.Goal}");

            collectProgressItem.SetData(Data.IngameTargetObjectID, Data.GameEvent.GetRewardId(_currData.Step), _currData.GetCurrCollectRate());
        }
        else
        {
            GameLog.Debug($"Not Exist _currData Step: {Data.CurrentStep}");
            _collectTargetProgressVar.fillAmount = 0f;
        }
    }

    private void OnProgressClearComplete()
    {
        _progressBarAnimation.gameObject.SetActive(false);
        DG.Tweening.DOTweenAnimation[] tweens = _progressBarAnimation.GetComponents<DG.Tweening.DOTweenAnimation>();
        foreach (var animation in tweens)
        {
            animation.DORewind();
            animation.DOPause();
        }
    }

    private void SetSMBearMoveRandomPlay(Action onComplete)
    {
        string playAniStr = $"Move{UnityEngine.Random.Range(1, 3)}";
        _smBearUI.PlayLoop(playAniStr);
        onComplete?.Invoke();
    }

    public override void OnClickBackButton()
    {
        if (Game.TouchGuardManager.isLocked)
            return;

        if (Data.IsLastStepClear()==false &&  Data.CanReceiveAnyReward())
        {
            //Data.RequestReward(null);
            Data.IsReservationReward = _isReservationReward = false;
            //Data.RequestReward(null);
			RequestReward();
		}
        base.OnClickBackButton();
    }

    public override void OnClickButtonClose()
    {
        if (Data.IsLastStepClear() == false && Data.CanReceiveAnyReward())
        {
            //Data.RequestReward(null);
            Data.IsReservationReward = _isReservationReward = false;
            //Data.RequestReward(null);
            RequestReward();

		}
        base.OnClickButtonClose();
    }
	public void RequestReward()
	{
        //if (!Data.GameEvent.IsActive)
        //{
        //          int Esn = Data.GameEvent.EventId;
        //	Data.RequestReward(() =>
        //	{
        //              GameLog.Warning($"//// Esn ===== {Esn} //// ");
        //		Game.EventManager.MarkAsNeedUpdate(EGameEvent.COLLECT);
        //		Game.EventManager.UpdateEventInfos(null);
        //		Game.EventManager.EventEndRemove(Esn);
        //	});
        //}
        //else
        //{
        //	Data.RequestReward(null);
        //}
        int Esn = 0;
        if(!Data.GameEvent.IsActive)
        {
			Esn = Data.GameEvent.EventId;
		}
		Data.RequestReward(() =>
		{
			GameLog.Warning($"//// Esn ===== {Esn} //// ");
			Game.EventManager.MarkAsNeedUpdate(EGameEvent.COLLECT);
			Game.EventManager.UpdateEventInfos(null);
            if (Esn != 0)
            {
				Game.EventManager.EventEndRemove(Esn);
			}

		});
	}
}
